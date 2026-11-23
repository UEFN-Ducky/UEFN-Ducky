"""Browser panes on Linux (gtk_panes): real WebKitGTK views in a Gtk.Window under Xvfb."""

from __future__ import annotations

import os
import sys
import threading
import time
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux") or not os.environ.get("DISPLAY"),
                                reason="Linux with an X display (xvfb-run)")


def _wait(pred, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture(scope="module")
def app_window():
    """A Gtk.Window laid out like pywebview's (a box holding the app's WebKit view), with
    the GTK main loop on its own thread. Yields the pywebview-like window (``native``)."""
    try:
        from frontend.native_host import gtk_panes as gp

        GLib, Gtk, WebKit2 = gp._gi()
    except (ImportError, ValueError) as exc:
        pytest.skip(f"needs python3-gi with Gtk 3 and WebKit2 4.1: {exc}")
    box: dict = {}
    up = threading.Event()

    def run() -> None:
        win = Gtk.Window()
        win.set_default_size(800, 600)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        host = WebKit2.WebView()
        host.load_html("<body style='background:#222'>app page</body>", "http://127.0.0.1/")
        page.pack_start(host, True, True, 0)
        win.add(page)
        win.show_all()
        box.update(win=win, host=host)
        up.set()
        Gtk.main()

    loop = threading.Thread(target=run, name="gtk-main", daemon=True)
    loop.start()
    assert up.wait(20)
    window = SimpleNamespace(native=box["win"])
    assert _wait(lambda: gp.ui_sync(window, lambda: box["host"].get_allocated_width()) >= 800)
    # The CSS viewport the app page reports: its view's size at zoom 1.
    window.viewport = gp.ui_sync(window, lambda: (float(box["host"].get_allocated_width()),
                                                  float(box["host"].get_allocated_height())))
    yield window
    # Ended and joined here: a GTK loop still running at interpreter exit aborts the process.
    GLib.idle_add(lambda: (box["win"].destroy(), Gtk.main_quit(), False)[-1])
    loop.join(10)


@pytest.fixture
def pushed(monkeypatch):
    from frontend.ui_web import browser_overlay as bo

    events: list[dict] = []
    monkeypatch.setattr(bo, "_push_page", lambda _w, e: events.append(dict(e)))
    monkeypatch.setattr(bo, "_mode", "memory")
    return events


def test_a_pane_opens_over_the_page_at_its_rect(app_window, pushed) -> None:
    from frontend.native_host import gtk_panes as gp
    from frontend.ui_web import browser_overlay as bo

    pane = bo._Pane(pane_id="lx1", window=app_window, pending_url="about:blank")
    pane.css_bounds, pane.css_viewport = (10.0, 20.0, 300.0, 200.0), app_window.viewport
    gp.ui(app_window, lambda: gp.create_control(pane))
    assert _wait(lambda: pane.ready), pane.failed
    assert any(e.get("pane_id") == "lx1" and e.get("ready") for e in pushed)

    def placed():
        v = pane.control.view
        ov = gp.overlay_of(app_window)
        return (v.get_parent() is ov, gp.host_view(ov) is not v, v.get_margin_start(), v.get_margin_top(),
                tuple(v.get_size_request()), v.get_visible())

    assert gp.ui_sync(app_window, placed) == (True, True, 10, 20, (300, 200), True)
    # The page under it is still the window's own view, inside the overlay.
    assert gp.ui_sync(app_window, lambda: app_window.native.get_child() is gp.overlay_of(app_window))

    # Moved and resized: the same view, new margins and size.
    pane.css_bounds = (40.0, 50.0, 400.0, 300.0)
    gp.ui(app_window, lambda: gp.apply_bounds(pane))
    assert gp.ui_sync(app_window, placed) == (True, True, 40, 50, (400, 300), True)

    # Hidden: off screen, still in the overlay. An agent's pane: drawn offscreen instead.
    pane.visible = False
    gp.ui(app_window, lambda: gp.apply_bounds(pane))
    assert gp.ui_sync(app_window, lambda: pane.control.view.get_visible()) is False
    pane.behind_until = time.monotonic() + 30
    gp.ui(app_window, lambda: gp.apply_bounds(pane))
    assert gp.ui_sync(app_window, lambda: pane.control.view.get_parent() is pane.control.offscreen)
    pane.visible, pane.behind_until = True, 0.0
    gp.ui(app_window, lambda: gp.apply_bounds(pane))
    assert gp.ui_sync(app_window, placed)[0] is True

    # Closed: the view is gone from the window.
    view = pane.control.view
    gp.ui_sync(app_window, pane.control.Dispose)
    assert gp.ui_sync(app_window, lambda: view not in gp.overlay_of(app_window).get_children())


def test_page_zoom_scales_the_rect(app_window, pushed) -> None:
    """CSS px map to the host view's pixels: a page that reports half the viewport is 2x."""
    from frontend.native_host import gtk_panes as gp
    from frontend.ui_web import browser_overlay as bo

    pane = bo._Pane(pane_id="lx2", window=app_window)
    vw, vh = app_window.viewport
    pane.css_bounds, pane.css_viewport = (10.0, 10.0, 100.0, 50.0), (vw / 2, vh / 2)
    gp.ui(app_window, lambda: gp.create_control(pane))
    assert _wait(lambda: pane.ready), pane.failed
    got = gp.ui_sync(app_window, lambda: (pane.control.view.get_margin_start(),
                                          tuple(pane.control.view.get_size_request())))
    assert got == (20, (200, 100))
    gp.ui_sync(app_window, pane.control.Dispose)


def test_ui_helpers_run_on_the_gtk_loop(app_window) -> None:
    from frontend.native_host import gtk_panes as gp

    assert gp.on_ui_thread() is False
    assert gp.ui_sync(app_window, gp.on_ui_thread) is True
    with pytest.raises(ZeroDivisionError):
        gp.ui_sync(app_window, lambda: 1 / 0)
