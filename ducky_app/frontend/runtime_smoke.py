"""Headless packaged-runtime check; uses synthetic data and no user configuration."""
from __future__ import annotations

import asyncio
import importlib.metadata
import json
import multiprocessing
import platform
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import anyio


async def _check_mcp() -> dict:
    from backend.mcp_plugins import client_pool as cp
    from backend.mcp_plugins.session_owner import OwnedSession

    scopes = []
    exited = []
    failed = [True]
    original_stdio, original_session, original_resolve = cp.stdio_client, cp.ClientSession, cp.resolve_server_block

    @asynccontextmanager
    async def transport(params):
        host = asyncio.current_task()
        async with anyio.create_task_group() as group:
            scopes.append(group.cancel_scope)
            group.start_soon(anyio.sleep_forever)
            try:
                yield None, None
            finally:
                assert asyncio.current_task() is host
                group.cancel_scope.cancel()
                exited.append(True)

    class Session:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def initialize(self):
            if failed[0]:
                raise ValueError("synthetic initialization failure")

        async def call_tool(self, name, args):
            return args

    cp.stdio_client, cp.ClientSession = transport, Session
    cp.resolve_server_block = lambda manifest: {"type": "stdio", "command": "unused", "args": []}
    pool = cp.PluginClientPool()
    try:
        for _ in range(5):
            conn = cp.PluginConnection("smoke", {})
            try:
                await pool._ensure_session(conn)
            except Exception:
                pass
            else:
                raise AssertionError("initialization should fail")
        failed[0] = False
        conn = cp.PluginConnection("smoke", {})
        pool._connections["smoke"] = conn
        session = await pool._ensure_session(conn)
        assert isinstance(session, OwnedSession)
        assert await session.call_tool("echo", {"value": 42}) == {"value": 42}
        # Close in a separate task, just like a later MCP caller or shutdown.
        await asyncio.create_task(pool.shutdown_all())
        for _ in range(3):
            await asyncio.sleep(0)
        assert len(exited) == 6
        assert all(not scope._tasks for scope in scopes)
        assert not pool._owners
        # The loop's own thread: a task left spinning runs here. process_time also counted every
        # other thread of the process (a pytest worker's leftovers on a loaded Mac runner).
        started, cpu = time.perf_counter(), time.thread_time()
        await asyncio.sleep(0.5)
        wall, used = time.perf_counter() - started, time.thread_time() - cpu
        assert used < 0.1, f"idle runtime used {used:.3f} CPU seconds during {wall:.3f}s"
        return {"contexts_closed": len(exited), "retained_scope_tasks": 0,
                "idle_wall_seconds": round(wall, 4), "idle_cpu_seconds": round(used, 4)}
    finally:
        await pool.shutdown_all()
        cp.stdio_client, cp.ClientSession, cp.resolve_server_block = original_stdio, original_session, original_resolve


def _check_gtk_window() -> dict:
    """Linux: the window is pywebview's GTK backend. Load it the way pywebview does and build
    (never show) a WebView, so a missing gi, typelib or host WebKitGTK fails smoke, not the GUI."""
    import sys

    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import Gtk, WebKit2

    if not Gtk.init_check(sys.argv[:1])[0]:
        raise RuntimeError("Gtk.init_check failed: no display")
    view = WebKit2.WebView()
    view.destroy()
    import webview.platforms.gtk  # noqa: F401  (what webview.start(gui="gtk") loads)

    tk_version = _check_tk()
    return {
        "gi": gi.__version__,
        "gtk": f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}",
        "webkit2": f"4.1 ({WebKit2.get_major_version()}.{WebKit2.get_minor_version()}.{WebKit2.get_micro_version()})",
        "tk": tk_version,
        "page_started_seconds": _start_real_window(),
    }


def _check_tk(environ=None) -> float:
    """webview_app imports tkinter on every start: a frozen _tkinter missing libXss.so.1 died
    there while this smoke still passed. The import loads _tkinter's libraries; ``Tk()`` needs
    an X display, so a pure Wayland session (no ``DISPLAY``) skips it."""
    import os
    import tkinter

    if (os.environ if environ is None else environ).get("DISPLAY"):
        tkinter.Tk().destroy()
    return tkinter.TkVersion


def webkit_version(bundle_version: str) -> str:
    """WebKit.framework's CFBundleVersion (``20620.1.16.11.8``: OS prefix + WebKit build) to
    the WebKit version Safari reports (``620.1``)."""
    parts = bundle_version.split(".")
    return ".".join([parts[0][-3:], *parts[1:2]])


def _check_cocoa_window(webview_mod=None, cocoa_mod=None, foundation=None) -> dict:
    """macOS: the window is pywebview's Cocoa backend (WKWebView through PyObjC). Load it, read
    the system WebKit's version and start the real window until its page loads."""
    if cocoa_mod is None:
        import webview.platforms.cocoa as cocoa_mod  # noqa: F401  (what webview.start(gui="cocoa") loads)
    if foundation is None:
        import Foundation as foundation
    bundle = foundation.NSBundle.bundleWithIdentifier_("com.apple.WebKit")
    if bundle is None:
        raise RuntimeError("WebKit.framework is not loaded")
    raw = str(bundle.objectForInfoDictionaryKey_("CFBundleVersion") or "")
    return {"webkit": webkit_version(raw), "page_started_seconds": _start_real_window(webview_mod=webview_mod, gui="cocoa")}


def _start_real_window(timeout: float = 60.0, webview_mod=None, gui: str = "gtk") -> float:
    """The real start: ``webview.start(gui="gtk", icon=<the app's icon>)`` until the page
    loads. A .ico icon made GTK create no window, which an unshown WebView never sees.
    Clean containers load in ~0.2 s; CI runners without a session bus wait out one 25 s
    D-Bus timeout first, hence the margin."""
    import threading

    from frontend.ui_web.webview_app import webview_icon_path

    if webview_mod is None:
        import webview as webview_mod
    started = time.perf_counter()
    loaded = threading.Event()
    window = webview_mod.create_window("UEFN Ducky smoke", html="<p>smoke</p>", hidden=False)
    window.events.loaded += loaded.set

    def watch() -> None:
        loaded.wait(timeout)
        window.destroy()

    webview_mod.start(watch, gui=gui, icon=str(webview_icon_path()))
    if not loaded.is_set():
        raise RuntimeError(f"window page did not start within {timeout:.0f}s")
    return round(time.perf_counter() - started, 2)


def check_panel_recovery(output: str) -> int:
    """Exercise the GUI's root selection without starting windows or services."""
    report = {"ok": False}
    try:
        from frontend.bundle_root import packaged_data_root
        from frontend.ui_web.panel_assets import verify_bundled_panel
        from frontend.ui_web.webview_app import _web_root

        data_root = packaged_data_root()
        if data_root is None:
            raise ValueError("Recovery smoke requires a packaged runtime")
        panel = data_root / "frontend" / "ui_web"
        selected = _web_root()
        verify_bundled_panel(selected, panel / "panel-dist.zip")
        # Exercise the same HTTP startup used by the GUI, on a private port.
        import urllib.request
        from frontend.ui_web import panel_httpd

        panel_httpd.PANEL_UI_HTTP_PORT = 0
        panel_httpd.start_panel_ui_server(selected)
        server = panel_httpd._server
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}"
            with urllib.request.urlopen(url + "/index.html", timeout=10) as response:
                if response.read() != (selected / "index.html").read_bytes():
                    raise ValueError("Panel server returned the wrong index")
            entry = next((selected / "assets").glob("*.js"))
            with urllib.request.urlopen(url + "/" + entry.relative_to(selected).as_posix(),
                                        timeout=10) as response:
                if response.read() != entry.read_bytes():
                    raise ValueError("Panel server returned the wrong JavaScript")
        finally:
            server.shutdown()
            server.server_close()
            panel_httpd._server = None
            panel_httpd._root = None
        report.update(ok=True, recovered=selected != panel / "web" / "dist")
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    Path(output).write_text(json.dumps(report), encoding="utf-8")
    return 0 if report["ok"] else 1


def run(output: str) -> int:
    report: dict = {"ok": False, "python": platform.python_version()}
    try:
        return _run(output, report)
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        Path(output).write_text(json.dumps(report, indent=2), encoding="utf-8")
        return 1


def _run(output: str, report: dict) -> int:
    # 1.2.133 shipped without backend.agent.prompt in the PYZ (SyntaxError during
    # freeze). Import these first so a missing module fails smoke, not the GUI.
    import backend.agent.hard_rules  # noqa: F401
    import backend.agent.prompt  # noqa: F401
    import backend.agent.runner  # noqa: F401
    import backend.store.db  # noqa: F401
    from backend.workspace.diff_workers import DiffWorkers
    from backend.workspace.paths import line_delta

    from frontend.bundle_root import packaged_data_root
    from frontend.ui_web.panel_assets import verify_bundled_panel

    # An EXE without truststore checks HTTPS with OpenSSL alone: "certificate has expired"
    # on PCs missing a newer root (Store downloads, updates). Never ship that.
    import truststore

    from frontend.ssl_boot import https_context

    assert isinstance(https_context(), truststore.SSLContext), "HTTPS is not verified by Windows"
    report["tls"] = {"truststore": truststore.__version__}

    data_root = packaged_data_root()
    if data_root is not None:
        panel = data_root / "frontend" / "ui_web"
        verify_bundled_panel(panel / "web" / "dist", panel / "panel-dist.zip")
        report["panel"] = {"all_assets_verified": True}

    report["versions"] = {
        name: importlib.metadata.version(name) for name in ("mcp", "anyio", "httpx")
    }
    report["mcp"] = asyncio.run(_check_mcp())
    from frontend import platform_info

    if platform_info.IS_LINUX:
        report["window"] = _check_gtk_window()
        print("runtime smoke: gi {gi}, Gtk {gtk}, WebKit2 {webkit2} ok".format(**report["window"]))
    elif platform_info.IS_MAC:
        cocoa = _check_cocoa_window()
        report.update(window=True, webkit=cocoa["webkit"], page_started_seconds=cocoa["page_started_seconds"])
        print(f"runtime smoke: Cocoa window, WebKit {cocoa['webkit']} ok")
    workers = DiffWorkers()
    before = "".join(f"Value_{n}:int = {n}\n" for n in range(10000))
    pairs = [(before, before.replace(f"Value_{n}:int = {n}", f"Value_{n}:int = 99999")) for n in range(4)]
    try:
        with ThreadPoolExecutor(max_workers=4) as callers:
            results = list(callers.map(lambda pair: workers.compare(*pair), pairs))
        assert results == [line_delta(*pair) for pair in pairs]
        child_count = len(multiprocessing.active_children())
    finally:
        workers.close()
    assert not multiprocessing.active_children()
    report["cpu_workers"] = {"jobs": 4, "child_processes": child_count, "matching_results": True, "children_after_close": 0}
    report["ok"] = True
    Path(output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0
