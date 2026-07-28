"""Self-check: Win+Shift+S snip path encodes clipboard images and reports busy."""

from __future__ import annotations

import base64
import io
from unittest.mock import patch


def test_png_result_roundtrip() -> None:
    from PIL import Image

    from frontend.ui_web.snip_overlay import _png_result

    img = Image.new("RGB", (12, 8), color=(10, 20, 30))
    result = _png_result(img)
    assert result["ok"] is True
    assert result["width"] == 12
    assert result["height"] == 8
    raw = base64.b64decode(result["data_base64"])
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    assert Image.open(io.BytesIO(raw)).size == (12, 8)


def test_busy_lock() -> None:
    from frontend.ui_web import snip_overlay as so

    assert so._session_lock.acquire(blocking=False)
    try:
        out = so.snip_screen_interactive(None)
        assert out == {"ok": False, "reason": "busy"}
    finally:
        so._session_lock.release()


def test_sendinput_failure() -> None:
    from frontend.ui_web import snip_overlay as so

    with (
        patch.object(so, "_clear_clipboard"),
        patch.object(so, "invoke_win_shift_s", return_value=False),
    ):
        out = so._run_win_shift_s()
    assert out["ok"] is False
    assert out["reason"] == "error"


def test_clipboard_accept() -> None:
    from PIL import Image

    from frontend.ui_web import snip_overlay as so

    img = Image.new("RGB", (4, 4), color=(1, 2, 3))
    with (
        patch.object(so, "_clear_clipboard"),
        patch.object(so, "invoke_win_shift_s", return_value=True),
        patch.object(so, "_grab_clipboard_image", return_value=img),
    ):
        out = so._run_win_shift_s()
    assert out["ok"] is True
    assert out["width"] == 4


if __name__ == "__main__":
    test_png_result_roundtrip()
    test_busy_lock()
    test_sendinput_failure()
    test_clipboard_accept()
    print("test_snip_overlay: ok")
