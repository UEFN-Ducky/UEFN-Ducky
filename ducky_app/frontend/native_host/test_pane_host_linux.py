"""Browser panes on Linux (gtk_panes + browser_overlay): real WebKitGTK views in a Gtk.Window
under Xvfb.

WebKitGTK only lives on a process's main thread (a view made on another thread aborts the
process at exit), as in the app, where pywebview runs GTK on the main thread. So each case
runs in a child process: GTK's loop on its main thread, the case on a worker thread, calling
in through the same helpers the panel's RPC threads use.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2]  # ducky_app/
NO_WEBKIT = 77

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux") or not os.environ.get("DISPLAY"),
                                reason="Linux with an X display (xvfb-run)")


def _wait(pred, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.1)
    return False


# --- cases (run in the child, on a worker thread) ----------------------------------------


def case_a_pane_opens_over_the_page_at_its_rect(window, pushed) -> None:
    from frontend.native_host import gtk_panes as gp
    from frontend.ui_web import browser_overlay as bo

    pane = bo._Pane(pane_id="lx1", window=window, pending_url="about:blank")
    pane.css_bounds, pane.css_viewport = (10.0, 20.0, 300.0, 200.0), window.viewport
    gp.ui(window, lambda: gp.create_control(pane))
    assert _wait(lambda: pane.ready), pane.failed
    assert any(e.get("pane_id") == "lx1" and e.get("ready") for e in pushed)

    def placed():
        v = pane.control.view
        ov = gp.overlay_of(window)
        return (v.get_parent() is ov, gp.host_view(ov) is not v, v.get_margin_start(), v.get_margin_top(),
                tuple(v.get_size_request()), v.get_visible())

    assert gp.ui_sync(window, placed) == (True, True, 10, 20, (300, 200), True)
    # The page under it is still the window's own view, inside the overlay.
    assert gp.ui_sync(window, lambda: window.native.get_child() is gp.overlay_of(window))

    # Moved and resized: the same view, new margins and size.
    pane.css_bounds = (40.0, 50.0, 400.0, 300.0)
    gp.ui(window, lambda: gp.apply_bounds(pane))
    assert gp.ui_sync(window, placed) == (True, True, 40, 50, (400, 300), True)

    # Hidden: off screen, still in the overlay. An agent's pane: drawn offscreen instead.
    pane.visible = False
    gp.ui(window, lambda: gp.apply_bounds(pane))
    assert gp.ui_sync(window, lambda: pane.control.view.get_visible()) is False
    pane.behind_until = time.monotonic() + 30
    gp.ui(window, lambda: gp.apply_bounds(pane))
    assert gp.ui_sync(window, lambda: pane.control.view.get_parent() is pane.control.offscreen)
    pane.visible, pane.behind_until = True, 0.0
    gp.ui(window, lambda: gp.apply_bounds(pane))
    assert gp.ui_sync(window, placed)[0] is True

    # Closed: the view is gone from the window (only the page is left in the overlay).
    gp.ui_sync(window, pane.control.Dispose)
    assert pane.control.view is None
    assert gp.ui_sync(window, lambda: len(gp.overlay_of(window).get_children())) == 1


def case_page_zoom_scales_the_rect(window, pushed) -> None:
    """CSS px map to the host view's pixels: a page that reports half the viewport is 2x."""
    from frontend.native_host import gtk_panes as gp
    from frontend.ui_web import browser_overlay as bo

    pane = bo._Pane(pane_id="lx2", window=window)
    vw, vh = window.viewport
    pane.css_bounds, pane.css_viewport = (10.0, 10.0, 100.0, 50.0), (vw / 2, vh / 2)
    gp.ui(window, lambda: gp.create_control(pane))
    assert _wait(lambda: pane.ready), pane.failed
    got = gp.ui_sync(window, lambda: (pane.control.view.get_margin_start(),
                                      tuple(pane.control.view.get_size_request())))
    assert got == (20, (200, 100)), got
    gp.ui_sync(window, pane.control.Dispose)


_FORM = """<title>Form</title><body style="margin:0;height:3000px">
<input id="q" style="position:absolute;left:20px;top:20px;width:200px;height:30px">
<script>window.keys = []; q.addEventListener('keydown', e => keys.push(e.key + ':' + e.isTrusted));</script>"""


def _site() -> str:
    """Loopback pages: /a and /b, each with its own title, and /form (a tall page with a box)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Page(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = (_FORM if self.path == "/form" else
                    f"<title>Page {self.path.strip('/').upper()}</title><p>{self.path}</p>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_a) -> None:
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


def case_browser_tab_opens_a_local_page_and_reads_its_title(window, pushed) -> None:
    """The Browser tab's calls (browser_overlay's public API) on Linux: open, bounds,
    navigate, back/forward, script, capture, close."""
    from frontend.native_host import gtk_panes as gp
    from frontend.ui_web import browser_overlay as bo

    site = _site()
    bo.configure(main_window=window)
    assert bo.open_pane("lxb", f"{site}/a")["ok"]
    assert bo.set_bounds("lxb", 0, 0, 400, 300, *window.viewport, True)["ok"]
    title = lambda want: _wait(lambda: bo.get_state("lxb").get("title") == want)  # noqa: E731
    assert title("Page A"), bo.get_state("lxb")
    assert bo.get_state("lxb")["url"] == f"{site}/a"
    assert any(e.get("pane_id") == "lxb" and e.get("title") == "Page A" for e in pushed)

    assert bo.navigate("lxb", f"{site}/b")["ok"]
    assert title("Page B") and _wait(lambda: bo.get_state("lxb")["can_back"])
    assert bo.command("lxb", "back")["ok"]
    assert title("Page A") and _wait(lambda: bo.get_state("lxb")["can_forward"])
    assert bo.command("lxb", "forward")["ok"]
    assert title("Page B")

    assert bo.evaluate("lxb", "document.title") == {"ok": True, "value": "Page B"}
    assert bo.evaluate("lxb", "Promise.resolve(6 * 7)") == {"ok": True, "value": 42}
    assert bo.evaluate("lxb", "nope()")["ok"] is False
    snap = bo.snapshot("lxb")
    assert snap["ok"] and snap["data"].startswith("data:image/jpeg;base64,"), snap
    assert bo.clear_browsing_data("cookies")["ok"]

    assert bo.navigate("lxb", "file:///etc/passwd")["ok"] is False
    assert bo.navigate("lxb", "http://127.0.0.1:5173/")["ok"] is False  # never the app inside itself

    assert bo.close_pane("lxb") == {"ok": True, "closed": True}
    assert _wait(lambda: gp.ui_sync(window, lambda: len(gp.overlay_of(window).get_children())) == 1)
    assert bo.get_state("lxb")["ok"] is False


def case_devtools_methods_click_type_keys_and_screenshot(window, pushed) -> None:
    """What the Browser plugin's agent tools send (browser_click, browser_type, browser_press,
    browser_screenshot), with Windows' reply shape."""
    import base64
    import struct

    from frontend.ui_web import browser_overlay as bo

    site = _site()
    bo.configure(main_window=window)
    dt = lambda method, **p: bo.devtools("lxd", method, p)  # noqa: E731
    js = lambda expr: bo.evaluate("lxd", expr)["value"]  # noqa: E731
    assert bo.open_pane("lxd", f"{site}/form")["ok"]
    assert bo.set_bounds("lxd", 0, 0, 400, 300, *window.viewport, True)["ok"]
    assert _wait(lambda: bo.get_state("lxd").get("title") == "Form" and not bo.get_state("lxd")["loading"])
    assert js("document.activeElement.id") != "q"

    # A click on the box focuses it (move, press, release, as browser_click sends them).
    assert dt("Emulation.setFocusEmulationEnabled", enabled=True) == {"ok": True, "reply": {}}
    for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
        extra = {} if kind == "mouseMoved" else {"button": "left", "clickCount": 1}
        assert dt("Input.dispatchMouseEvent", type=kind, x=60, y=35, **extra)["ok"]
    assert _wait(lambda: js("document.activeElement.id") == "q"), js("document.activeElement.tagName")

    # Typed text lands in the box; Enter is a trusted keydown the page sees.
    assert dt("Input.insertText", text="hi") == {"ok": True, "reply": {}}
    assert dt("Input.dispatchKeyEvent", type="keyDown", key="Enter", code="Enter", windowsVirtualKeyCode=13, text="\r")["ok"]
    assert dt("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter", windowsVirtualKeyCode=13)["ok"]
    assert _wait(lambda: js("q.value") == "hi" and "Enter:true" in js("keys")), (js("q.value"), js("keys"))

    # browser_type with clear: select all, then the new text replaces the old.
    select_all = {"key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65, "modifiers": 2}
    assert dt("Input.dispatchKeyEvent", type="rawKeyDown", commands=["selectAll"], **select_all)["ok"]
    assert dt("Input.dispatchKeyEvent", type="keyUp", **select_all)["ok"]
    assert dt("Input.insertText", text="x")["ok"]
    assert _wait(lambda: js("q.value") == "x"), js("q.value")

    # The whole tall page, as base64 PNG in reply.data.
    got = dt("Page.captureScreenshot", format="png")
    assert got["ok"], got
    png = base64.b64decode(got["reply"]["data"])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", png[16:24])
    assert h >= 3000 > window.viewport[1] and w >= 300, (w, h)
    assert dt("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=1, mobile=True)["ok"]
    assert dt("Emulation.clearDeviceMetricsOverride")["ok"]

    nope = dt("Network.getCookies")
    assert nope["ok"] is False and "Network.getCookies" in nope["error"]
    assert bo.close_pane("lxd")["ok"]
    assert bo.devtools("lxd", "Page.captureScreenshot") == {"ok": False, "error": "pane not open"}


def case_ui_helpers_run_on_the_gtk_loop(window, pushed) -> None:
    from frontend.native_host import gtk_panes as gp

    assert gp.on_ui_thread() is False
    assert gp.ui_sync(window, gp.on_ui_thread) is True
    try:
        gp.ui_sync(window, lambda: 1 / 0)
    except ZeroDivisionError:
        pass
    else:
        raise AssertionError("ui_sync must raise what the call raised")


CASES = sorted(n for n in dir() if n.startswith("case_"))


# --- the child ---------------------------------------------------------------------------


def _child(name: str) -> int:
    """A window laid out like pywebview's (a box holding the app's WebKit view), GTK on this
    main thread, case ``name`` on a worker."""
    from types import SimpleNamespace

    try:
        from frontend.native_host import gtk_panes as gp

        GLib, Gtk, WebKit2 = gp._gi()
    except (ImportError, ValueError) as exc:
        print(f"no WebKitGTK: {exc}")
        return NO_WEBKIT
    from frontend.ui_web import browser_overlay as bo

    pushed: list[dict] = []
    bo._push_page = lambda _w, e: pushed.append(dict(e))
    bo._mode = "memory"
    win = Gtk.Window()
    win.set_default_size(800, 600)
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    host = WebKit2.WebView()
    host.load_html("<body style='background:#222'>app page</body>", "http://127.0.0.1/")
    page.pack_start(host, True, True, 0)
    win.add(page)
    win.show_all()
    window = SimpleNamespace(native=win)
    ok: list[bool] = []

    def work() -> None:
        try:
            assert _wait(lambda: gp.ui_sync(window, host.get_allocated_width) >= 800)
            # The CSS viewport the app page reports: its view's size at zoom 1.
            window.viewport = gp.ui_sync(window, lambda: (float(host.get_allocated_width()),
                                                          float(host.get_allocated_height())))
            globals()[name](window, pushed)
            ok.append(True)
        except BaseException:
            traceback.print_exc()
        finally:
            GLib.idle_add(Gtk.main_quit)

    threading.Thread(target=work, name=name, daemon=True).start()
    Gtk.main()
    return 0 if ok else 1


@pytest.mark.parametrize("name", CASES)
def test_case(name: str) -> None:
    env = {**os.environ, "PYTHONPATH": str(APP), "WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS": "1"}
    proc = subprocess.run([sys.executable, __file__, name], cwd=APP, env=env, capture_output=True,
                          text=True, timeout=120)
    if proc.returncode == NO_WEBKIT:
        pytest.skip(proc.stdout.strip() or "needs python3-gi with Gtk 3 and WebKit2 4.1")
    assert proc.returncode == 0, proc.stdout + proc.stderr


if __name__ == "__main__":
    sys.exit(_child(sys.argv[1]))
