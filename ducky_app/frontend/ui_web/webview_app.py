"""PyWebView shell for the React control panel."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from frontend.bundle_root import is_packaged_runtime, packaged_data_root
from frontend.frozen_process import (
    claim_panel_process,
    reclaim_stale_panel_process,
)
from frontend.settings import PANEL_LISTENER_PORT
from frontend.tray_icon import TrayIconController, tray_supported
from frontend.ui_web.shutdown import request_app_exit, start_tk_pump


def _web_root() -> Path:
    if is_packaged_runtime():
        base = packaged_data_root()
        if base:
            candidate = base / "frontend" / "ui_web" / "web" / "dist"
            if (candidate / "index.html").is_file():
                return candidate
    return Path(__file__).resolve().parent / "web" / "dist"


def _connection_mode_from_status(cached: dict | None) -> str:
    if isinstance(cached, dict):
        if bool(cached.get("wedged")):
            return "wedged"
        if bool(cached.get("online")):
            return "online"
        return "offline"

    from backend.bridge import listener_get_health


    health = listener_get_health(PANEL_LISTENER_PORT)
    if health is not None and health.get("status") == "ok":
        return "online"
    return "offline"


def _apply_shell_icons(api: object, mode: str) -> None:
    from frontend.ui_web import focus_windows

    focus_windows.set_connection_mode(mode)
    tray = getattr(api, "_tray", None)
    if tray:
        tray.set_connection_mode(mode)
    window = getattr(api, "_window_holder", {}).get("window")
    if window is not None:
        try:
            from frontend.ui_web.win_frameless import set_window_icon_hwnd

            set_window_icon_hwnd(window, mode=mode)
        except Exception:
            pass


def _poll_listener(api: object) -> None:
    """Tray + taskbar icon heartbeat — reuse cached status from the UI poll when possible."""
    last_mode: str | None = None
    while True:
        try:
            cached = getattr(api, "_last_listener_status", None)
            mode = _connection_mode_from_status(cached if isinstance(cached, dict) else None)
            if mode != last_mode:
                last_mode = mode
                _apply_shell_icons(api, mode)
        except Exception:
            pass
        time.sleep(8)


def _boot_trace(name: str, t0: float, **meta: object) -> None:
    """Record one cold-start phase; never raises."""
    try:
        from frontend.perf_trace import ensure_started, trace

        ensure_started()
        duration_ms = (time.perf_counter() - t0) * 1000.0
        total_ms = None
        raw = os.environ.get("UEFN_DUCKY_BOOT_T0")
        if raw:
            try:
                total_ms = round((time.perf_counter() - float(raw)) * 1000.0, 1)
            except ValueError:
                pass
        payload = dict(meta)
        if total_ms is not None:
            payload["total_ms"] = total_ms
        trace("boot", name, duration_ms=duration_ms, **payload)
    except Exception:
        pass


def run() -> None:
    import sys

    from frontend.open_files import (
        cli_deep_links,
        cli_open_paths,
        enqueue_deep_links,
        enqueue_open_paths,
        read_living_panel_pid,
        register_windows_open_with,
        register_windows_url_protocol,
        try_handoff_to_running,
    )

    open_paths = cli_open_paths(sys.argv)
    deep_links = cli_deep_links(sys.argv)
    # Second launch (Open with / drag onto EXE / shortcut while running): hand off
    # to the living panel instead of killing it and starting a duplicate window.
    t_handoff = time.perf_counter()
    if read_living_panel_pid() is not None:
        # Short budget only — stale panel.pid after update/force-kill must not stall
        # cold start for tens of seconds on connect timeouts.
        if try_handoff_to_running(open_paths, links=deep_links):
            _boot_trace("handoff_exit", t_handoff)
            try:
                from frontend.early_splash import dismiss as dismiss_early_splash

                dismiss_early_splash()
            except Exception:
                pass
            raise SystemExit(0)
        # Fall through: reclaim kills the wedged instance, then we start fresh.
    _boot_trace("handoff", t_handoff)

    if is_packaged_runtime():
        t_reclaim = time.perf_counter()
        reclaim_stale_panel_process()
        claim_panel_process()
        _boot_trace("reclaim_claim", t_reclaim)
        # Registry writes are not needed to paint the window — off the critical path.
        def _register_shell_integrations() -> None:
            register_windows_open_with()
            register_windows_url_protocol()

        threading.Thread(
            target=_register_shell_integrations,
            daemon=True,
            name="register-open-with",
        ).start()

    # Cold start: queue CLI paths / deep links until React mounts and consumes them.
    enqueue_open_paths(open_paths)
    enqueue_deep_links(deep_links)

    try:
        _run_panel(api_holder := {"api": None, "tk_root": None, "window_holder": {}})
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:
        from frontend.ui_web.shutdown import fatal_error_and_exit

        fatal_error_and_exit(
            exc,
            api=api_holder.get("api"),
            tk_root=api_holder.get("tk_root"),
        )


def _run_panel(api_holder: dict[str, object]) -> None:
    # The WinForms UI thread runs Python callbacks (pywebview bridge, evaluate_js
    # completions) and competes for the GIL with agent threads doing CPU-bound
    # stream/JSON work. The default 5ms switch interval lets busy threads starve
    # it for seconds (perf traces showed 14-18s evaluate_js waits with tiny
    # payloads). A shorter interval keeps the UI thread responsive.
    import sys as _sys

    _sys.setswitchinterval(0.001)
    try:
        from frontend.appdata_maintenance import start_appdata_maintenance_async
        from frontend.ship_newest import ship_newest_everywhere_async

        # Listener + skills + Cursor/Claude/Antigravity — newest for this EXE, off UI thread
        # so the window paints immediately instead of blocking 15–20s on sync/copytrees.
        # ide_apply / merge skip writes when mcp.json is already current (avoids Cursor
        # "Reloading model…" reconnect mid-chat).
        ship_newest_everywhere_async(apply_ides=True, force_skills=False)
        start_appdata_maintenance_async()
        try:
            from frontend.perf_trace import ensure_started

            ensure_started()
        except Exception:
            pass

        def _startup_bg() -> None:
            try:
                from frontend.settings import PanelSettings, apply_workspace_env
                from frontend.ui_web.project_files import purge_undo_trash

                # A restart forgets the in-memory undo registry, so last session's trash is dead weight.
                purge_undo_trash()
                apply_workspace_env(PanelSettings.load().uefn_project_root)
            except Exception:
                pass
            try:
                from frontend.ui_web.project_deploy import deploy_all_recent_projects

                deploy_all_recent_projects()
            except Exception:
                pass

        def _presence_bg() -> None:
            try:
                from frontend.duckyos_account import start_presence_heartbeat

                start_presence_heartbeat()
            except Exception:
                pass

        threading.Thread(target=_startup_bg, daemon=True, name="panel-startup-bg").start()
        threading.Thread(target=_presence_bg, daemon=True, name="duckyos-presence-start").start()
    except Exception:
        pass

    # WebView2 paints this color for not-yet-rendered regions (e.g. area newly exposed while
    # resizing), instead of stark white. Must be set before the WebView2 environment is created;
    # the env var is more reliable than the DefaultBackgroundColor property (WebView2Feedback
    # BackgroundColor spec). ARGB hex — match the dark shell / boot splash (#0a0a0a).
    os.environ.setdefault("WEBVIEW2_DEFAULT_BACKGROUND_COLOR", "FF0A0A0A")

    t_webview = time.perf_counter()
    import tkinter as tk
    import webview

    from frontend.ui_web.win_frameless import (
        ensure_app_user_model_id,
        install_pywebview_chrome_patches,
        install_sync_drag_bridge,
    )

    ensure_app_user_model_id()
    install_pywebview_chrome_patches()
    install_sync_drag_bridge()

    webview.settings["DRAG_REGION_SELECTOR"] = ".pywebview-native-drag-disabled"
    webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = False
    _boot_trace("webview_import_patches", t_webview)

    # Keep the early logo splash up through heavy imports — create the pump Tk later
    # by converting that same splash root (avoids a second Tcl interpreter).
    t_import_api = time.perf_counter()
    from frontend.ui_web.panel_api import PanelApi

    _boot_trace("import_panel_api", t_import_api)

    t_api_init = time.perf_counter()
    api = PanelApi()
    _boot_trace("panel_api_init", t_api_init)
    api_holder["api"] = api
    api._tray = None  # type: ignore[attr-defined]
    window_holder: dict[str, object] = {}
    api_holder["window_holder"] = window_holder
    api._window_holder = window_holder  # type: ignore[attr-defined]
    # Filled just before webview.start once the splash becomes the pump root.
    tk_root_holder: dict[str, object] = {"root": None}

    # Register BEFORE the panel HTTP server starts so a second-instance Open-with
    # handoff never hits a window where the endpoint exists but the handler does not.
    try:
        from frontend.open_files import (
            enqueue_deep_links,
            enqueue_open_paths,
            set_deep_link_handler,
            set_open_files_handler,
        )
        from frontend.ui_web.file_drop_import import _notify_deep_links, _notify_open_external

        def _show_window() -> object | None:
            w = window_holder.get("window")
            if w is None:
                return None
            try:
                w.show()
            except Exception:
                pass
            try:
                w.restore()
            except Exception:
                pass
            return w

        def _on_open_files(paths: list[str]) -> None:
            # Always queue too: CustomEvent can fire before React's listener mounts.
            if paths:
                enqueue_open_paths(paths)
            w = _show_window()
            if w is not None and paths:
                _notify_open_external(w, paths)

        def _on_deep_links(links: list[str]) -> None:
            if links:
                enqueue_deep_links(links)
            w = _show_window()
            if w is not None and links:
                _notify_deep_links(w, links)

        set_open_files_handler(_on_open_files)
        set_deep_link_handler(_on_deep_links)
    except Exception:
        pass

    def on_exit() -> None:
        request_app_exit(
            api=api,
            tk_root=tk_root_holder.get("root"),
            window_holder=window_holder,
        )

    def on_hide() -> None:
        w = window_holder.get("window")
        if w is not None:
            try:
                w.hide()
            except Exception:
                pass

    def restore() -> None:
        w = window_holder.get("window")
        if w is not None:
            try:
                w.show()
            except Exception:
                pass

    api.bind_window(None, on_hide=on_hide, on_exit=on_exit)

    bundled_index = _web_root() / "index.html"
    if not bundled_index.is_file():
        raise FileNotFoundError(
            "React build not found. Run: cd ducky_app/frontend/ui_web/web && npm ci && npm run build "
            "or set UEFN_DUCKY_WEB_DEV=http://localhost:5173"
        )
    t_httpd = time.perf_counter()
    from frontend.ui_web.panel_httpd import start_panel_ui_server
    from frontend.ui_web.web_dev import is_dev_panel, resolve_web_url

    web_root = bundled_index.parent
    bundled_http = start_panel_ui_server(web_root)
    url, web_debug = resolve_web_url(
        bundled_index_uri=bundled_index.as_uri(),
        bundled_http_url=bundled_http,
    )
    _boot_trace("http_server", t_httpd)
    title = "UEFN Ducky (Dev)" if is_dev_panel() else "UEFN Ducky"
    from frontend.ui_web import window_bounds

    saved = window_bounds.get_bounds("main")
    t_window = time.perf_counter()
    window = webview.create_window(
        title,
        url=url,
        width=saved["width"] if saved else 920,
        height=saved["height"] if saved else 640,
        x=saved["x"] if saved else None,
        y=saved["y"] if saved else None,
        min_size=(700, 480),
        resizable=True,
        frameless=True,
        easy_drag=False,
        # Match boot splash / critical.css so the chrome never flashes white.
        background_color="#0a0a0a",
        js_api=api,
    )
    window_bounds.track(window, "main")
    window_holder["window"] = window
    api.bind_window(window, on_hide=on_hide, on_exit=on_exit)
    _boot_trace("create_window", t_window)

    # Copy files dropped from Explorer onto the Content tree (hooks window.events.loaded).
    try:
        from frontend.ui_web.file_drop_import import install_file_drop_import

        install_file_drop_import(window, api)
    except Exception:
        pass

    from frontend.ui_web import browser_overlay, focus_windows

    focus_windows.configure(api=api, base_url=url.split("?")[0], main_window=window)
    browser_overlay.configure(main_window=window)

    def _refresh_window_chrome() -> None:
        try:
            from frontend.ui_web.win_frameless import refresh_window_chrome, reset_webview_pin

            refresh_window_chrome(window)
            reset_webview_pin(window)
        except Exception:
            pass

    def _on_maximized() -> None:
        api._maximized = True
        _refresh_window_chrome()

    def _on_restored() -> None:
        api._maximized = False
        _refresh_window_chrome()

    window.events.maximized += _on_maximized
    window.events.restored += _on_restored

    def _apply_window_icon() -> None:
        # Set the runtime HICON so the taskbar / Alt-Tab / Task Manager show the app icon (not just
        # the .exe file icon). The native handle may not exist the instant 'shown' fires, so retry.
        try:
            from frontend.ui_web.win_frameless import refresh_window_chrome, set_window_icon_hwnd
        except Exception:
            return
        cached = getattr(api, "_last_listener_status", None)
        mode = _connection_mode_from_status(cached if isinstance(cached, dict) else None)
        for _ in range(20):
            if refresh_window_chrome(window, pin=True) and set_window_icon_hwnd(window, mode=mode):
                return
            time.sleep(0.1)

    def _on_shown() -> None:
        threading.Thread(target=_apply_window_icon, daemon=True, name="window-icon").start()

    window.events.shown += _on_shown

    threading.Thread(target=_poll_listener, args=(api,), daemon=True, name="listener-poll").start()

    def on_closing() -> bool:
        on_hide()
        try:
            from frontend.ui_web.editor_workspace import flush_focus_windows_to_disk

            flush_focus_windows_to_disk()
        except Exception:
            pass
        return False

    window.events.closing += on_closing

    from frontend.tray_icon import resolve_app_icon_path
    from frontend.ui_web.ui_dispatch import ensure_started

    ensure_started()

    # Splash → hidden pump root (same Tk), then tray, then the real window.
    try:
        from frontend.early_splash import take_as_pump_root

        tk_root = take_as_pump_root()
    except Exception:
        tk_root = None
    if tk_root is None:
        tk_root = tk.Tk()
        tk_root.withdraw()
    tk_root_holder["root"] = tk_root
    api_holder["tk_root"] = tk_root
    api.bind_tk_root(tk_root)
    start_tk_pump(tk_root)

    if tray_supported():
        try:
            api._tray = TrayIconController(  # type: ignore[attr-defined]
                tk_root,
                on_show=restore,
                on_exit=on_exit,
                tooltip="UEFN Ducky",
                status="offline",
            )
            api._tray.run_daemon()  # type: ignore[attr-defined]
        except Exception:
            api._tray = None  # type: ignore[attr-defined]

    icon_path = resolve_app_icon_path()
    _boot_trace("ready_for_webview_start", time.perf_counter())
    # Production: enable WebView2 DevTools so ErrorBoundary "Open Inspector" / F12 work.
    # Do not auto-pop the inspector window on every launch.
    try:
        webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = False
    except Exception:
        pass
    # debug=True always: AreDevToolsEnabled so ErrorBoundary can open Inspector.
    # OPEN_DEVTOOLS_IN_DEBUG=False above keeps it from auto-opening on launch.
    _ = web_debug  # still used by resolve_web_url for Vite vs bundled URL
    webview.start(
        debug=True,
        icon=str(icon_path) if icon_path else None,
    )
