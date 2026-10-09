"""Appearance Google fonts: downloaded once into AppData, served locally, never outside it."""

from __future__ import annotations

from pathlib import Path

import pytest

from frontend.ui_web import font_cache

_CSS = (
    "@font-face { font-family: 'Lobster'; src: url(https://fonts.gstatic.com/s/lobster/v1/a.woff2) format('woff2'); }\n"
    "@font-face { font-family: 'Lobster'; src: url(https://fonts.gstatic.com/s/lobster/v1/b.woff2) format('woff2'); }\n"
)


@pytest.fixture
def fonts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setattr(font_cache, "fonts_dir", lambda: tmp_path / "fonts")
    fetched: list[str] = []

    def fake_get(url: str, limit: int) -> bytes:
        fetched.append(url)
        if url.startswith(font_cache._CSS_HOST):
            return _CSS.encode()
        return b"wOF2" + url.encode()

    monkeypatch.setattr(font_cache, "_get", fake_get)
    return fetched


def test_font_downloads_once_and_points_at_the_local_server(fonts: list[str]) -> None:
    first = font_cache.cache_google_font("Lobster")
    assert first == {"ok": True, "href": "/__fonts/lobster/font.css", "cached": False}
    css = font_cache.font_file("lobster", "font.css").read_text(encoding="utf-8")
    assert "gstatic" not in css and "googleapis" not in css
    assert "url(/__fonts/lobster/0.woff2)" in css and "url(/__fonts/lobster/1.woff2)" in css
    assert font_cache.font_file("lobster", "1.woff2").read_bytes().startswith(b"wOF2")

    again = font_cache.cache_google_font("Lobster")
    assert again["cached"] is True
    assert len(fonts) == 3  # one stylesheet + two font files, only the first time


def test_bad_names_and_paths_are_refused(fonts: list[str]) -> None:
    assert font_cache.cache_google_font("../evil")["ok"] is False
    assert font_cache.cache_google_font("")["ok"] is False
    assert fonts == []
    font_cache.cache_google_font("Lobster")
    assert font_cache.font_file("..", "font.css") is None
    assert font_cache.font_file("lobster", "../font.css") is None
    assert font_cache.font_file("lobster", "evil.js") is None
    assert font_cache.font_file("missing", "font.css") is None


def test_a_failed_download_leaves_nothing_behind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(font_cache, "fonts_dir", lambda: tmp_path / "fonts")

    def fake_get(url: str, limit: int) -> bytes:
        if url.startswith(font_cache._CSS_HOST):
            return _CSS.encode()
        raise OSError("offline")

    monkeypatch.setattr(font_cache, "_get", fake_get)
    res = font_cache.cache_google_font("Lobster")
    assert res["ok"] is False and "offline" in res["error"]
    assert not (tmp_path / "fonts" / "lobster").exists()
    assert not (tmp_path / "fonts" / "lobster.part").exists()


def test_unknown_family_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(font_cache, "fonts_dir", lambda: tmp_path / "fonts")
    monkeypatch.setattr(font_cache, "_get", lambda url, limit: b"/* no fonts */")
    res = font_cache.cache_google_font("Not A Real Font")
    assert res["ok"] is False and "no font named" in res["error"]
