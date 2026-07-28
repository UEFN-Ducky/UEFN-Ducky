"""ext: scheme lets read_project_file view an arbitrary dragged-in file read-only,
without requiring a project or a workspace-folder gate."""

from __future__ import annotations

import pytest

import frontend.ui_web.project_files as pf
from frontend.ui_web.project_files import (
    EXT_PATH_PREFIX,
    read_project_file,
    write_external_file,
)


def _ext(path) -> str:
    return f"{EXT_PATH_PREFIX}{path}"


def test_read_ext_file_returns_text(tmp_path):
    f = tmp_path / "notes.md"
    f.write_text("# Hello\nexternal file\n", encoding="utf-8")
    result = read_project_file(_ext(f))
    assert result["content"] == "# Hello\nexternal file\n"
    assert result["path"].lower().startswith(EXT_PATH_PREFIX)
    assert "binary_preview" not in result


def test_read_ext_file_missing_raises(tmp_path):
    with pytest.raises(ValueError):
        read_project_file(_ext(tmp_path / "does-not-exist.md"))


def test_read_ext_file_too_large_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(pf, "_TEXT_OPEN_MAX_BYTES", 8)
    f = tmp_path / "big.txt"
    f.write_text("more than eight bytes", encoding="utf-8")
    with pytest.raises(ValueError):
        read_project_file(_ext(f))


def test_read_ext_binary_returns_hex_preview(tmp_path):
    # NUL bytes in a non-editable suffix → hex preview, never a full text read.
    b = tmp_path / "blob.bin"
    b.write_bytes(b"\x00\x01\x02BINARY")
    result = read_project_file(_ext(b))
    assert result.get("binary_preview") == "true"


def test_write_ext_file_round_trips(tmp_path):
    f = tmp_path / "player.verse"
    f.write_text("original\n", encoding="utf-8")
    result = write_external_file(_ext(f), "edited in place\n")
    assert result["path"].lower().startswith(EXT_PATH_PREFIX)
    assert f.read_text(encoding="utf-8") == "edited in place\n"
    # Re-reading through the ext: reader returns the new content.
    assert read_project_file(_ext(f))["content"] == "edited in place\n"


def test_write_ext_missing_folder_raises(tmp_path):
    with pytest.raises(ValueError):
        write_external_file(_ext(tmp_path / "gone" / "x.verse"), "x")


def test_write_ext_too_large_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(pf, "_TEXT_OPEN_MAX_BYTES", 8)
    f = tmp_path / "big.verse"
    f.write_text("start\n", encoding="utf-8")
    with pytest.raises(ValueError):
        write_external_file(_ext(f), "way more than eight bytes")
    # Failed (too-large) write must NOT truncate the original.
    assert f.read_text(encoding="utf-8") == "start\n"


def test_write_ext_rejects_non_ext_path():
    with pytest.raises(ValueError):
        write_external_file("Content/Foo.verse", "x")


def test_write_ext_rejects_png(tmp_path):
    """Saving through the text bridge must never overwrite an image."""
    import base64

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    f = tmp_path / "pixel.png"
    f.write_bytes(png)
    with pytest.raises(ValueError):
        write_external_file(_ext(f), "corrupt")
    assert f.read_bytes() == png


def test_read_ext_png_is_image_not_hex(tmp_path):
    import base64

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    f = tmp_path / "pixel.png"
    f.write_bytes(png)
    result = read_project_file(_ext(f))
    assert result["kind"] == "image"
    assert result.get("binary_preview") != "true"
    assert "media_url" in result
