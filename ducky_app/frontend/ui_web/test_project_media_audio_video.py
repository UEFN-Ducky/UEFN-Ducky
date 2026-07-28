"""resolve_project_media_path / read_project_file should accept audio & video files."""

from __future__ import annotations

from frontend.ui_web.project_files import EXT_PATH_PREFIX, read_project_file
from frontend.ui_web.project_media import resolve_project_media_path


def _ext(path) -> str:
    return f"{EXT_PATH_PREFIX}{path}"


def test_resolve_project_media_path_accepts_audio(tmp_path):
    mp3 = tmp_path / "theme.mp3"
    mp3.write_bytes(b"ID3fake-mp3-bytes")

    encoded = _ext(mp3)
    from urllib.parse import quote, unquote

    token = quote(encoded, safe="")
    resolved = resolve_project_media_path(token)
    assert resolved.resolve() == mp3.resolve()
    assert unquote(token) == encoded


def test_resolve_project_media_path_accepts_video(tmp_path):
    mp4 = tmp_path / "intro.mp4"
    mp4.write_bytes(b"fake-mp4-bytes")

    from urllib.parse import quote

    encoded = _ext(mp4)
    token = quote(encoded, safe="")
    resolved = resolve_project_media_path(token)
    assert resolved.resolve() == mp4.resolve()


def test_resolve_project_media_path_rejects_non_media(tmp_path):
    import pytest

    txt = tmp_path / "notes.txt"
    txt.write_text("hello")

    from urllib.parse import quote

    encoded = _ext(txt)
    token = quote(encoded, safe="")
    with pytest.raises(ValueError, match="Not a media file"):
        resolve_project_media_path(token)


def test_read_project_file_returns_media_url_for_audio(tmp_path):
    mp3 = tmp_path / "theme.mp3"
    mp3.write_bytes(b"ID3fake-mp3-bytes")

    result = read_project_file(_ext(mp3))
    assert result["kind"] == "audio"
    assert result["mime"] == "audio/mpeg"
    assert result["media_url"].startswith("http://127.0.0.1:")
    assert "/project-media/" in result["media_url"]


def test_read_project_file_returns_media_url_for_video(tmp_path):
    mp4 = tmp_path / "cutscene.mp4"
    mp4.write_bytes(b"fake-mp4-bytes")

    result = read_project_file(_ext(mp4))
    assert result["kind"] == "video"
    assert result["mime"] == "video/mp4"
    assert result["media_url"].startswith("http://127.0.0.1:")
