"""The last session's picture of Ducky: stored, checked, shown at start and closed when the page paints."""
from __future__ import annotations

import inspect
import json
import struct
import time

from frontend import boot_snapshot as bs
from frontend import snapshot_splash


def _pixels(width: int, height: int, colourful: bool = True) -> bytes:
    out = bytearray()
    for y in range(height):
        for x in range(width):
            if colourful:
                out += bytes(((x * 7) % 256, (y * 5) % 256, ((x + y) * 3) % 256, 255))
            else:
                out += b"\x0a\x0a\x0a\xff"
    return bytes(out)


def test_bmp_is_bottom_up_32bpp_and_keeps_rows_in_order():
    w, h = 3, 2
    top_down = bytes(range(w * h * 4))
    data = bs.bmp_bytes(w, h, top_down)
    assert data[:2] == b"BM"
    assert struct.unpack_from("<ii", data, 18) == (w, h)
    assert struct.unpack_from("<H", data, 28)[0] == 32
    pixels = data[54:]
    row = w * 4
    assert pixels[:row] == top_down[row:]  # bottom row first
    assert pixels[row:] == top_down[:row]


def test_saved_picture_loads_back_with_its_place(tmp_path):
    w, h = 320, 240
    assert bs.save((100, 50, w, h), _pixels(w, h), folder=tmp_path, version="1.2.471") is True
    snap = bs.load(folder=tmp_path)
    assert snap is not None
    assert (snap["x"], snap["y"], snap["w"], snap["h"]) == (100, 50, w, h)
    assert bs.bmp_size(tmp_path / bs.BMP_NAME) == (w, h)


def test_a_blank_capture_is_not_kept(tmp_path):
    w, h = 320, 240
    assert bs.save((0, 0, w, h), _pixels(w, h, colourful=False), folder=tmp_path) is False
    assert bs.load(folder=tmp_path) is None


def test_odd_sizes_old_pictures_and_broken_files_are_not_shown(tmp_path):
    assert bs.save((0, 0, 50, 50), _pixels(50, 50), folder=tmp_path) is False  # too small
    w, h = 320, 240
    bs.save((0, 0, w, h), _pixels(w, h), folder=tmp_path)
    assert bs.load(folder=tmp_path, now=time.time() + bs.MAX_AGE_S + 60) is None
    meta = tmp_path / bs.META_NAME
    record = json.loads(meta.read_text(encoding="utf-8"))
    meta.write_text(json.dumps(dict(record, w=w + 1)), encoding="utf-8")  # size no longer matches the BMP
    assert bs.load(folder=tmp_path) is None
    meta.write_text("{not json", encoding="utf-8")
    assert bs.load(folder=tmp_path) is None
    bs.forget(folder=tmp_path)
    assert not (tmp_path / bs.BMP_NAME).exists() and not meta.exists()


def test_capture_refuses_no_window():
    assert bs.capture_window(0) is False


def test_dismiss_without_a_picture_is_a_no_op():
    snapshot_splash._state["hwnd"] = 0
    snapshot_splash.dismiss()
    assert snapshot_splash.is_showing() is False


def test_launch_shows_the_picture_before_falling_back_to_the_logo():
    from frontend import launcher

    src = inspect.getsource(launcher.main)
    assert src.index("snapshot_splash.show(snapshot)") < src.index("show_early_splash()")
    assert "if not shown_snapshot:" in src


def test_picture_closes_on_paint_hand_off_and_hidden_start_and_is_saved_before_hiding():
    from frontend.ui_web import panel_httpd, webview_app

    httpd_src = inspect.getsource(panel_httpd)
    assert 'payload.get("painted")' in httpd_src and "dismiss_snapshot()" in httpd_src
    run_src = inspect.getsource(webview_app.run)
    assert run_src.index("dismiss_snapshot()") < run_src.index("raise SystemExit(0)", run_src.index("dismiss_snapshot()"))
    panel_src = inspect.getsource(webview_app._run_panel)
    hidden = panel_src.index("if start_hidden:")
    assert "dismiss_snapshot()" in panel_src[hidden:hidden + 200]
    closing = panel_src[panel_src.index("def on_closing"):]
    assert closing.index("_save_boot_snapshot()") < closing.index("on_hide()")
