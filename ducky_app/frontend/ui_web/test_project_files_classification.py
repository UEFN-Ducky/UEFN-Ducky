"""Classification + image/text open behavior for project files."""

from __future__ import annotations

import base64

import pytest

from frontend.ui_web.file_kinds import (
    classify_project_file,
    is_known_text_filename,
    should_show_dot_entry,
)
from frontend.ui_web.project_files import (
    EXT_PATH_PREFIX,
    is_editable_text_file,
    read_project_file,
    write_external_file,
)


def _ext(path) -> str:
    return f"{EXT_PATH_PREFIX}{path}"


# Minimal 1x1 PNG
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_classify_gitignore_and_makefile():
    assert classify_project_file("Content/.gitignore")["kind"] == "text"
    assert classify_project_file("Makefile")["kind"] == "text"
    assert classify_project_file("Dockerfile")["kind"] == "text"
    assert is_known_text_filename(".gitignore")
    assert is_known_text_filename("LICENSE")


def test_classify_images_and_binaries():
    assert classify_project_file("shot.png")["kind"] == "image"
    assert classify_project_file("photo.JPEG")["kind"] == "image"
    assert classify_project_file("mesh.fbx")["kind"] == "model"
    assert classify_project_file("hero.glb")["kind"] == "model"
    assert classify_project_file("prop.obj")["kind"] == "model"
    assert classify_project_file("a.uasset")["kind"] == "unreal_asset"
    assert classify_project_file("lib.dll")["kind"] == "binary"
    assert classify_project_file("texture.tga")["kind"] == "binary"


def test_read_ext_fbx_returns_model_media(tmp_path):
    f = tmp_path / "cube.fbx"
    f.write_bytes(b"Kaydara FBX Binary  \x00")
    result = read_project_file(_ext(f))
    assert result["kind"] == "model"
    assert result["content"] == ""
    assert "/model-files/" in result["media_url"]
    assert result["media_filename"] == "cube.fbx"
    assert result["media_base_url"].endswith("/")
    assert not is_editable_text_file(_ext(f))


def test_write_ext_rejects_model(tmp_path):
    f = tmp_path / "cube.fbx"
    f.write_bytes(b"Kaydara FBX Binary  \x00")
    with pytest.raises(ValueError, match="cannot be saved"):
        write_external_file(_ext(f), "nope")


def test_classify_extensionless_sniff():
    assert classify_project_file("NOTES", head=b"hello world\n")["kind"] == "text"
    assert classify_project_file("NOTES", head=b"\x00\x01\x02")["kind"] == "binary"


def test_dotfile_visibility():
    assert should_show_dot_entry(".gitignore", False, show_hidden=False) is True
    assert should_show_dot_entry(".mystery", False, show_hidden=False) is False
    assert should_show_dot_entry(".mystery", False, show_hidden=True) is True
    assert should_show_dot_entry(".git", True, show_hidden=True) is False


def test_read_ext_gitignore_as_text(tmp_path):
    f = tmp_path / ".gitignore"
    f.write_text("*.uasset\n", encoding="utf-8")
    result = read_project_file(_ext(f))
    assert result["kind"] == "text"
    assert result["content"] == "*.uasset\n"
    assert "binary_preview" not in result
    assert is_editable_text_file(_ext(f))


def test_read_ext_png_returns_media_url(tmp_path):
    f = tmp_path / "pixel.png"
    f.write_bytes(_PNG_BYTES)
    result = read_project_file(_ext(f))
    assert result["kind"] == "image"
    assert result["content"] == ""
    assert result["media_url"].startswith("http://127.0.0.1:")
    assert "/project-media/" in result["media_url"]
    assert not is_editable_text_file(_ext(f))


def test_write_ext_rejects_image(tmp_path):
    f = tmp_path / "pixel.png"
    f.write_bytes(_PNG_BYTES)
    with pytest.raises(ValueError, match="cannot be saved"):
        write_external_file(_ext(f), "nope")


def test_write_ext_rejects_binary(tmp_path):
    f = tmp_path / "blob.bin"
    f.write_bytes(b"\x00\x01\x02BINARY")
    with pytest.raises(ValueError, match="cannot be saved"):
        write_external_file(_ext(f), "nope")


def test_read_ext_extensionless_utf8(tmp_path):
    f = tmp_path / "NOTES"
    f.write_text("plain notes\n", encoding="utf-8")
    result = read_project_file(_ext(f))
    assert result["kind"] == "text"
    assert result["content"] == "plain notes\n"
    assert is_editable_text_file(_ext(f))


def test_read_ext_binary_still_hex(tmp_path):
    b = tmp_path / "blob.bin"
    b.write_bytes(b"\x00\x01\x02BINARY")
    result = read_project_file(_ext(b))
    assert result.get("binary_preview") == "true"
    assert result["kind"] == "binary"


def test_resolve_project_media_path_sandbox(tmp_path):
    from frontend.ui_web.project_media import resolve_project_media_path
    from urllib.parse import quote

    f = tmp_path / "ok.png"
    f.write_bytes(_PNG_BYTES)
    path = resolve_project_media_path(quote(_ext(f), safe=""))
    assert path == f.resolve()

    with pytest.raises(ValueError):
        resolve_project_media_path(quote(_ext(tmp_path / "nope.txt"), safe=""))
