"""Tool pictures fit every model API, so a screenshot never ends a turn with a 400."""

from __future__ import annotations

import base64
import io
import os
from pathlib import Path

import pytest
from PIL import Image

from backend.tools.core import model_picture as mp


def _sent(picture) -> Image.Image:
    content = picture.to_image_content()
    return Image.open(io.BytesIO(base64.b64decode(content.data))), content.mimeType


def test_a_small_png_goes_as_it_is(tmp_path: Path) -> None:
    path = tmp_path / "shot.png"
    Image.new("RGB", (800, 600), "red").save(path)
    picture = mp.model_picture(path)
    assert picture.path == path and picture.to_image_content().mimeType == "image/png"


def test_a_4k_screenshot_is_scaled_to_the_many_picture_limit(tmp_path: Path) -> None:
    path = tmp_path / "window.png"
    Image.new("RGB", (3840, 2160), "blue").save(path)
    img, mime = _sent(mp.model_picture(path))
    assert img.size == (1920, 1080) and mime == "image/png"


def test_a_bmp_is_sent_as_png_not_octet_stream(tmp_path: Path) -> None:
    path = tmp_path / "icon.bmp"
    Image.new("RGB", (64, 64), "green").save(path)
    img, mime = _sent(mp.model_picture(path))
    assert mime == "image/png" and img.size == (64, 64)


def test_a_picture_too_big_as_png_is_sent_as_jpeg_under_the_limit(tmp_path: Path) -> None:
    path = tmp_path / "noise.png"
    Image.frombytes("RGB", (1900, 1900), os.urandom(1900 * 1900 * 3)).save(path)
    assert path.stat().st_size > mp.MAX_BYTES
    picture = mp.model_picture(path)
    img, mime = _sent(picture)
    assert mime == "image/jpeg" and len(picture.data) <= mp.MAX_BYTES


def test_a_broken_file_is_a_clear_error_not_a_broken_picture(tmp_path: Path) -> None:
    path = tmp_path / "bad.png"
    path.write_bytes(b"not a png")
    with pytest.raises(ValueError, match="Not a picture Ducky can open: bad.png"):
        mp.model_picture(path)
