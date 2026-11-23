"""Desktop spotlight geometry: pin a box to a corner, follow a move or resize, keep the card on the monitor."""

from __future__ import annotations

from frontend.ui_web.window_spotlight import anchor_box, clean_box, pad_hole, place_card, project_box


def test_box_fractions_clamp_into_the_window():
    assert clean_box({"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}) == {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}
    assert clean_box({"x": -1, "y": 0.9, "w": 0.5, "h": 2}) == {"x": 0.0, "y": 0.0, "w": 0.5, "h": 1.0}
    assert clean_box({"x": 0, "y": 0, "w": 0, "h": 0.2}) is None
    assert clean_box("nope") is None


def test_anchor_follows_a_move_and_sticks_to_the_nearest_corner():
    toolbar = anchor_box({"x": 0.02, "y": 0.01, "w": 0.1, "h": 0.05}, 1000, 800)
    assert toolbar["ax"] == "left" and toolbar["ay"] == "top"
    x, y, _w, _h = project_box(toolbar, 100, 50, 1000, 800)
    assert (x, y) == (120, 58)
    moved = project_box(toolbar, 400, 200, 1000, 800)
    assert (moved[0] - 400, moved[1] - 200) == (x - 100, y - 50)
    wider = project_box(toolbar, 100, 50, 1600, 800)
    assert wider[0] == 120

    details = anchor_box({"x": 0.8, "y": 0.2, "w": 0.15, "h": 0.4}, 1000, 800)
    assert details["ax"] == "right"
    x, _y, w, _h = project_box(details, 0, 0, 1000, 800)
    gap = 1000 - (x + w)
    x2, _y2, w2, _h2 = project_box(details, 0, 0, 1400, 900)
    assert 1400 - (x2 + w2) == gap


def test_hole_is_padded_and_round_like_the_in_app_spotlight():
    assert pad_hole(100, 100, 50, 20) == (92, 92, 66, 36, 12)
    x, y, w, h, radius = pad_hole(100, 100, 20, 10)
    assert (w, h) == (36, 36)
    assert radius == 18
    assert (x, y) == (92, 87)


def test_card_stays_on_the_monitor_and_off_the_hole():
    hole = (100, 100, 40, 20)
    monitor = (0, 0, 800, 600)
    x, y = place_card(hole, monitor, 200, 120)
    assert (x, y) == (152, 100)
    assert x + 200 <= 800 and y + 120 <= 600

    # No room on the right: the card goes to the left, still on the monitor.
    x, y = place_card((700, 100, 80, 20), monitor, 200, 120)
    assert x >= 0 and x + 200 <= 800
    assert y >= 0 and y + 120 <= 600
    assert x + 200 <= 700


# ---- pixels: the desktop card, ring and dim drawn like the in-app Show me popup ----


def test_card_pixels_match_the_popup_layout_and_expose_its_buttons():
    from frontend.ui_web.window_spotlight_paint import CARD_W, SURFACE, CardState, render_card

    px = render_card(CardState(title="Your custom water materials", body="Both are **here**: `Materials`.", index=1, total=3))
    pad_l, pad_t, pad_r, pad_b = px.pad
    assert px.visible[0] == CARD_W
    assert px.image.size == (pad_l + CARD_W + pad_r, pad_t + px.visible[1] + pad_b)
    # Opaque surface in the middle, fully transparent far corner (so a click there passes through).
    assert px.image.getpixel((pad_l + CARD_W // 2, pad_t + 40))[:3] == SURFACE
    assert px.image.getpixel((0, 0))[3] == 0
    assert set(px.hits) == {"close", "next", "back"}
    cx0, cy0, cx1, cy1 = px.hits["close"]
    assert cx1 - cx0 == 28 and cy1 - cy0 == 28
    assert cx1 <= pad_l + CARD_W and cy0 >= pad_t - 4
    assert px.hit(cx0 + 14, cy0 + 14) == "close"
    assert px.hit(pad_l + 20, pad_t + 20) is None

    # One step: no footer, no Next/Back; a click step keeps Next off until the last step.
    single = render_card(CardState(title="T", body="B", index=0, total=1))
    assert set(single.hits) == {"close"} and single.visible[1] < px.visible[1]
    clicky = render_card(CardState(title="T", body="B", index=0, total=2, click=True))
    assert "next" not in clicky.hits
    last = render_card(CardState(title="T", body="B", index=1, total=2, click=True))
    assert "next" in last.hits  # it reads Close there


def test_hovering_a_button_changes_its_pixels_only():
    from frontend.ui_web.window_spotlight_paint import CardState, render_card

    plain = render_card(CardState(title="T", body="B", index=0, total=1))
    hover = render_card(CardState(title="T", body="B", index=0, total=1, hover="close"))
    x0, y0, x1, y1 = plain.hits["close"]
    assert plain.image.getpixel((x0 + 3, y0 + 3)) != hover.image.getpixel((x0 + 3, y0 + 3))
    pad_l, pad_t, _r, _b = plain.pad
    assert plain.image.getpixel((pad_l + 30, pad_t + 60)) == hover.image.getpixel((pad_l + 30, pad_t + 60))


def test_dim_is_clear_inside_the_hole_and_dark_outside():
    from frontend.ui_web.window_spotlight_paint import DIM_ALPHA, render_dim

    dim = render_dim(400, 300, (100, 100, 120, 80), 12)
    assert dim.size == (400, 300)
    assert dim.getpixel((160, 140))[3] == 0
    assert dim.getpixel((10, 10))[3] == DIM_ALPHA
    assert dim.getpixel((100, 100))[3] == DIM_ALPHA  # rounded corner stays dim
    assert 0 < dim.getpixel((104, 104))[3] < DIM_ALPHA  # anti-aliased edge


def test_ring_glows_outside_and_stays_clear_inside():
    from frontend.ui_web.window_spotlight_paint import render_ring

    ring, margin = render_ring(120, 80, 12)
    assert ring.size == (120 + 2 * margin, 80 + 2 * margin)
    assert ring.getpixel((ring.width // 2, ring.height // 2))[3] == 0
    assert ring.getpixel((margin + 1, ring.height // 2))[3] > 200
    assert 0 < ring.getpixel((margin - 6, ring.height // 2))[3] < 200
    assert ring.getpixel((0, 0))[3] == 0


def test_clear_rect_and_text_helpers():
    from PIL import Image

    from frontend.ui_web.window_spotlight_paint import clear_rect, font, plain_text, wrap

    img = Image.new("RGBA", (50, 50), (1, 2, 3, 255))
    out = clear_rect(img, (10, 10, 20, 20))
    assert out.getpixel((15, 15))[3] == 0 and out.getpixel((5, 5))[3] == 255
    assert img.getpixel((15, 15))[3] == 255  # the input is untouched
    assert plain_text("Line one\n- item **bold** `code`\n\nSecond para") == ["Line one • item bold code", "Second para"]
    fnt = font("regular", 13)
    lines = wrap("a few words that will certainly not fit on one short line", fnt, 120)
    assert len(lines) > 1 and all(fnt.getlength(line) <= 120 for line in lines)
    assert wrap("", fnt, 100) == [""]


def test_a_click_on_the_highlight_advances_even_when_it_fits_between_two_polls():
    from frontend.ui_web.window_spotlight import click_in_hole

    # Press seen inside the hole, release seen inside it: advance on the release.
    advance, armed = click_in_hole(False, False, True, True, True)
    assert (advance, armed) == (False, True)
    assert click_in_hole(armed, True, False, True, True) == (True, False)
    # Released outside the hole: nothing, and the step is disarmed.
    assert click_in_hole(True, True, False, True, False) == (False, False)
    # A press that started outside the hole never arms it.
    assert click_in_hole(False, False, True, True, False) == (False, False)
    # Down and up both happened between polls: the "pressed since" flag with the
    # pointer in the hole is that click.
    assert click_in_hole(False, False, False, True, True) == (True, False)
    assert click_in_hole(False, False, False, True, False) == (False, False)
    # Nothing pressed: nothing happens.
    assert click_in_hole(False, False, False, False, True) == (False, False)


# ---- Linux ---------------------------------------------------------------------

import os
import sys

import pytest


def test_alpha_rects_cover_exactly_the_visible_pixels_in_few_rectangles():
    from PIL import Image

    from frontend.ui_web.window_spotlight_paint import alpha_rects, render_dim

    dim = render_dim(400, 300, (100, 80, 60, 40), 12)
    rects = alpha_rects(dim.getchannel("A"), 70)
    assert len(rects) < 120  # identical rows merge; the hole's round corners add a few
    covered = Image.new("L", dim.size, 0)
    for x, y, w, h in rects:
        covered.paste(255, (x, y, x + w, y + h))
    want = dim.getchannel("A").point(lambda a: 255 if a >= 70 else 0)
    assert covered.tobytes() == want.tobytes()
    assert covered.getpixel((130, 100)) == 0 and covered.getpixel((5, 5)) == 255
    assert alpha_rects(Image.new("L", (8, 8), 0), 1) == []


def test_x11_layer_opacity_and_no_blackout_without_a_compositor():
    from frontend.ui_web.window_spotlight_paint import render_card, render_dim, x11_layer, CardState

    dim = render_dim(200, 100, (50, 30, 40, 20), 8)
    _rgb, opacity, rects = x11_layer(dim, composited=True)
    assert abs(opacity - 140 / 255) < 0.01 and rects
    assert x11_layer(dim, composited=False)[2] == []  # never a black screen
    card = render_card(CardState(title="Settings", body="Open it here", index=0, total=1)).image
    _rgb, opacity, rects = x11_layer(card, composited=False)
    assert opacity == 1.0 and rects  # the card shows with or without a compositor


def test_monitor_containing_picks_the_screen_under_the_point():
    from frontend.ui_web.window_spotlight import monitor_containing

    mons = [(0, 0, 1920, 1080), (1920, 0, 3840, 1440)]
    assert monitor_containing(mons, 2000, 50, (0, 0, 1, 1)) == (1920, 0, 3840, 1440)
    assert monitor_containing(mons, -5, 50, (0, 0, 1, 1)) == (0, 0, 1, 1)


def test_wayland_says_the_desktop_spotlight_is_in_app_only(monkeypatch):
    from frontend.ui_web import ui_rpc
    from frontend.ui_web import window_spotlight as ws

    monkeypatch.setattr(ws.platform_info, "IS_LINUX", True)
    monkeypatch.setattr(ws.platform_info, "session_type", lambda: "wayland")
    out = ws.prepare_window_show([{"box": {"x": 0, "y": 0, "w": 0.1, "h": 0.1}}])
    assert out["reason"] == "wayland" and "own window" in out["error"]
    answers: list = []
    monkeypatch.setattr(ui_rpc, "respond", lambda rid, payload: answers.append((rid, payload)))
    ws.begin_window_show("r1", {"steps": []})
    assert answers == [("r1", {"ok": False, "error": ws.WAYLAND_NOTE, "reason": "wayland"})]


def _x11_tk_ready() -> bool:
    import shutil

    if not sys.platform.startswith("linux") or not shutil.which("Xvfb"):
        return False
    try:
        import tkinter  # noqa: F401

        import Xlib  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.fixture
def own_display(monkeypatch):
    """A private Xvfb and a fresh shared Xlib connection: xdist workers on one display race."""
    import subprocess

    from frontend.ui_web import window_spotlight as ws

    read_fd, write_fd = os.pipe()
    server = subprocess.Popen(
        ["Xvfb", "-displayfd", str(write_fd), "-screen", "0", "640x480x24", "-nolisten", "tcp"],
        pass_fds=(write_fd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    os.close(write_fd)
    number = b""
    while not number.endswith(b"\n"):
        chunk = os.read(read_fd, 16)
        if not chunk:
            break
        number += chunk
    os.close(read_fd)
    if not number.strip():
        server.kill()
        pytest.fail("Xvfb did not start")
    monkeypatch.setenv("DISPLAY", ":" + number.decode().strip())
    monkeypatch.setattr(ws, "_x_disp", None)
    yield
    if ws._x_disp is not None:
        ws._x_disp.close()
        ws._x_disp = None
    server.terminate()
    server.wait(5)


_NEEDS_X = pytest.mark.skipif(not _x11_tk_ready(), reason="needs Linux, Xvfb, tkinter and python-xlib")


@_NEEDS_X
def test_x11_overlay_window_is_shaped_click_through_and_on_top(own_display):
    # Tk loads its own libX11 state; in an xdist worker other tests already made Tk / GTK / Xlib
    # connections, and on ubuntu-22.04 that combination took the worker down. A fresh child
    # process (on the fixture's private display) runs the real X calls and reports back.
    import subprocess
    from pathlib import Path

    app_root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": str(app_root) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    child = subprocess.run(
        [sys.executable, "-c", "from frontend.ui_web.test_window_spotlight import _overlay_case; _overlay_case(); print('overlay ok')"],
        cwd=app_root, env=env, capture_output=True, text=True, timeout=60,
    )
    assert child.returncode == 0 and "overlay ok" in child.stdout, (child.returncode, child.stdout, child.stderr)


def _overlay_case() -> None:
    import tkinter as tk

    from PIL import Image
    from Xlib.ext import shape

    from frontend.ui_web import window_spotlight as ws
    from frontend.ui_web.window_spotlight_paint import LayeredWindow

    root = tk.Tk()
    root.withdraw()
    try:
        ring = LayeredWindow(root, click_through=True)
        image = Image.new("RGBA", (60, 40), (0, 0, 0, 0))
        image.paste((76, 141, 255, 255), (10, 10, 50, 30))
        ring.paint(image, 30, 20)
        ring.show(True)
        ring.raise_()
        root.update()
        d = ws._xd()
        win = d.create_resource_object("window", ring.hwnd)
        assert win.get_attributes().override_redirect
        bounding = win.shape_get_rectangles(shape.SK.Bounding).rectangles
        assert [(r.x, r.y, r.width, r.height) for r in bounding] == [(10, 10, 40, 20)]
        assert win.shape_get_rectangles(shape.SK.Input).rectangles == []
        assert ws._window_box(ring.hwnd) == (30, 20, 90, 60)
        ring.destroy()
    finally:
        root.destroy()


@_NEEDS_X
def test_x11_pointer_button_and_escape_reads(own_display):
    from Xlib import X, XK
    from Xlib.ext import xtest

    from frontend.ui_web import window_spotlight as ws

    d = ws._xd()
    d.screen().root.warp_pointer(123, 45)
    d.sync()
    assert ws._cursor() == (123, 45)
    assert ws._key_down(0x01) is False
    xtest.fake_input(d, X.ButtonPress, 1)
    d.sync()
    assert ws._key_down(0x01) is True and ws._button_state(0x01) == (True, False)
    xtest.fake_input(d, X.ButtonRelease, 1)
    esc = d.keysym_to_keycode(XK.XK_Escape)
    xtest.fake_input(d, X.KeyPress, esc)
    d.sync()
    assert ws._key_down(0x1B) is True
    xtest.fake_input(d, X.KeyRelease, esc)
    d.sync()
    assert ws._key_down(0x1B) is False
    left, top, right, bottom = ws._monitor_of(10, 10)
    assert (left, top) == (0, 0) and right > 0 and bottom > 0

