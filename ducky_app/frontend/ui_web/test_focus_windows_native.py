"""Real hidden WebView2 focus-window lifecycle, isolated from the user's app.

Run as a subprocess through pytest: a native access violation fails the test.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="native WinForms/WebView2 integration")
def test_real_focus_windows_repeated_open_close_and_same_name_chats(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    env["LOCALAPPDATA"] = str(tmp_path)
    env["APPDATA"] = str(tmp_path / "Roaming")
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--native-smoke"],
        env=env, capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FOCUS_CYCLES_OK=12" in result.stdout
    assert "INTERPRETER_TEARDOWN" not in result.stdout, "the smoke must end like the app (see _smoke)"


def _smoke():
    import atexit
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import webview
    from frontend.ui_web import focus_windows as focus, win_frameless as chrome
    from frontend.ui_web import tab_registry, window_bounds, webview_memory

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<html><body>Isolated focus lifecycle test</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_port}/"
    chrome.install_pywebview_chrome_patches()
    chrome.install_sync_drag_bridge()
    real_create = webview.create_window

    def hidden_create(*args, **kwargs):
        kwargs["hidden"] = True
        return real_create(*args, **kwargs)

    webview.create_window = hidden_create
    focus._bring_to_front = lambda _w: None
    focus._log_close = lambda *args: None
    window_bounds.track = lambda *args: None
    window_bounds.get_bounds = lambda *args: None
    main = real_create("Ducky isolated lifecycle smoke", url=base_url, hidden=True,
                       frameless=True, easy_drag=False)
    webview_memory.install(main)
    focus.configure(api=None, base_url=base_url, main_window=main)
    failures = []

    def wait_until(predicate):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        raise AssertionError("Native lifecycle condition did not complete")

    def run():
        try:
            wait_until(lambda: main.native is not None)
            for cycle in range(12):
                tab = ["chat:a", "chat:b", "settings:main", "file:example.verse"][cycle % 4]
                group = focus._create_group_with_tab(tab, "Same name")
                window = group.window
                wait_until(lambda: window.native is not None)
                hwnd = chrome._hwnd_from_pywebview(window)
                assert hwnd and chrome.refresh_window_chrome(window)
                assert tab_registry.find_tab_owner(tab) == group.wid
                # Exercise WebView2 and bridge replies before disposing its owner.
                window.events.loaded.wait(12)
                assert window.evaluate_js("1 + 1") == 2
                focus.close_focus_window(tab, window_id="focus-old")
                assert focus.window_for_focus_id(tab) is window
                if cycle % 3 == 0:
                    focus.close_focus_window(tab, window_id=group.wid)
                elif cycle % 3 == 1:
                    focus.close_window(window, reason="smoke header close", return_tabs=True)
                else:
                    window.destroy()  # OS/native close event path
                wait_until(lambda: focus.window_for_focus_id(tab) is None)
                wait_until(lambda: hwnd not in chrome._native_subclass_orig)
            assert not focus.list_focus_window_ids()
            print("FOCUS_CYCLES_OK=12", flush=True)
        except BaseException as exc:
            failures.append(exc)
        finally:
            main.destroy()

    atexit.register(print, "INTERPRETER_TEARDOWN", flush=True)
    webview.start(run, gui="edgechromium", private_mode=True)
    server.shutdown()
    if failures:
        raise failures[0]
    # End like the app (shutdown.py), never through interpreter teardown. A focus window
    # closed while its dispatch worker was inside WebView2's Invoke leaves that call
    # waiting; WinForms throws into it once the UI thread is gone, and if that lands in
    # teardown pythonnet can't convert the exception and the CLR ends the process with
    # 0xE0434352 after every cycle passed (2 of 4 full runs on Oct 20 2026).
    from frontend.ui_web.shutdown import _terminate_process

    _terminate_process()


def test_mac_focus_window_reopens_at_its_saved_frame_and_raises(monkeypatch):
    """The macOS path with fake Cocoa: the saved frame goes to create_window, the raise and the
    cursor go through AppKit (top-left points, like pywebview's window.x / y)."""
    import types

    import webview
    from frontend.native_host import launcher_windows
    from frontend.ui_web import focus_windows as focus, mac_frameless, webview_memory, window_bounds

    made: list[dict] = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(launcher_windows, "active", lambda: False)
    monkeypatch.setattr(window_bounds, "get_bounds", lambda key: {"x": 40, "y": 60, "width": 800, "height": 500}
                        if key == "focus:chat:a" else None)
    monkeypatch.setattr(window_bounds, "track", lambda *a: None)
    monkeypatch.setattr(webview_memory, "install", lambda *a: None)
    monkeypatch.setattr(webview, "create_window", lambda title, **kw: made.append(kw) or types.SimpleNamespace(**kw))
    focus._create_focus_window("chat:a", "A", "A", "focus-1")
    focus._create_focus_window("chat:b", "B", "B", "focus-2")
    assert [(k["x"], k["y"], k["width"], k["height"]) for k in made] == [(40, 60, 800, 500), (None, None, 720, 520)]
    assert made[0]["frameless"] is True and made[0]["easy_drag"] is False

    later, calls = [], []
    native = types.SimpleNamespace(makeKeyAndOrderFront_=lambda _s: calls.append("key"))
    app = types.SimpleNamespace(activateIgnoringOtherApps_=lambda on: calls.append(("active", on)))
    screen = types.SimpleNamespace(frame=lambda: types.SimpleNamespace(size=types.SimpleNamespace(height=900.0)))
    appkit = types.ModuleType("AppKit")
    appkit.NSApplication = types.SimpleNamespace(sharedApplication=lambda: app)
    appkit.NSScreen = types.SimpleNamespace(screens=lambda: [screen])
    appkit.NSEvent = types.SimpleNamespace(mouseLocation=lambda: types.SimpleNamespace(x=300.0, y=700.0))
    helpers = types.ModuleType("PyObjCTools")
    helpers.AppHelper = types.SimpleNamespace(callAfter=later.append)
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setitem(sys.modules, "PyObjCTools", helpers)
    focus._force_foreground(types.SimpleNamespace(native=native))
    later.pop()()
    assert calls == [("active", True), "key"]
    assert focus._cursor_screen_point() == (300, 200)  # 900 - 700: y counted down from the top

    moved = types.SimpleNamespace(native=native, resize=lambda w, h: calls.append(("size", w, h)),
                                  move=lambda x, y: calls.append(("move", x, y)))
    from frontend.ui_web.win_frameless import set_window_bounds_hwnd

    assert set_window_bounds_hwnd(moved, 10, 20, 640, 480) is True
    assert calls[-2:] == [("size", 640, 480), ("move", 10, 20)]
    assert mac_frameless.set_bounds(types.SimpleNamespace(native=None), 0, 0, 1, 1) is False


@pytest.mark.skipif(sys.platform != "darwin", reason="native Cocoa/WKWebView integration")
def test_real_focus_windows_on_cocoa_restore_their_frame(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2]), "HOME": str(tmp_path)}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--native-smoke-mac"],
                            env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FOCUS_MAC_OK=4" in result.stdout


def _smoke_mac():
    """Hidden Cocoa windows: each focus window opens where its saved bounds say, then closes."""
    import time

    import webview
    from frontend.ui_web import focus_windows as focus, window_bounds

    real_create = webview.create_window
    webview.create_window = lambda *a, **kw: real_create(*a, **{**kw, "hidden": True})
    focus._bring_to_front = lambda _w: None
    window_bounds.track = lambda *args: None
    window_bounds.get_bounds = lambda key: {"x": 120, "y": 140, "width": 700, "height": 460}
    main = real_create("Ducky mac focus smoke", html="<p>main</p>", hidden=True, frameless=True, easy_drag=False)
    focus.configure(api=None, base_url="about:blank", main_window=main)
    failures: list[BaseException] = []

    def wait_until(predicate):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        raise AssertionError("Cocoa lifecycle condition did not complete")

    def run():
        try:
            for cycle in range(4):
                tab = f"chat:{cycle}"
                window = focus._create_group_with_tab(tab, "Same name").window
                wait_until(lambda: window.native is not None)
                window.events.loaded.wait(12)
                assert (window.width, window.height) == (700, 460), (window.width, window.height)
                assert (window.x, window.y) == (120, 140), (window.x, window.y)
                assert window.evaluate_js("1 + 1") == 2
                focus.close_window(window, reason="smoke", return_tabs=True)
                wait_until(lambda: focus.window_for_focus_id(tab) is None)
            print("FOCUS_MAC_OK=4", flush=True)
        except BaseException as exc:
            failures.append(exc)
        finally:
            main.destroy()

    webview.start(run, gui="cocoa", private_mode=True)
    if failures:
        raise failures[0]


if __name__ == "__main__":
    _smoke_mac() if "--native-smoke-mac" in sys.argv else _smoke()
