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


def _smoke():
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

    webview.start(run, gui="edgechromium", private_mode=True)
    server.shutdown()
    if failures:
        raise failures[0]


if __name__ == "__main__":
    _smoke()
