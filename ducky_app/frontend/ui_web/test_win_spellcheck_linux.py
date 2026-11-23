"""Linux spellcheck: WebKitGTK gets the user's languages from the locale variables."""

from __future__ import annotations

import sys

import pytest

from frontend.ui_web.win_spellcheck import enable_linux, linux_languages


def test_languages_from_locale_variables() -> None:
    env = {"LANGUAGE": "fr_CA:fr", "LANG": "ja_JP.UTF-8", "LC_MESSAGES": "C"}
    assert linux_languages(env) == ["fr_CA", "fr", "ja_JP", "en_US"]
    assert linux_languages({"LANG": "de_DE.UTF-8@euro"}) == ["de_DE", "en_US"]
    assert linux_languages({}) == ["en_US"]


class _Ctx:
    def __init__(self) -> None:
        self.enabled, self.langs = False, []

    def set_spell_checking_enabled(self, on: bool) -> None:
        self.enabled = on

    def set_spell_checking_languages(self, langs) -> None:
        self.langs = list(langs)

    def get_spell_checking_languages(self):
        return self.langs


class _View:
    def __init__(self) -> None:
        self.ctx = _Ctx()

    def get_context(self):
        return self.ctx

    def load_uri(self, uri) -> None:  # marks it as a WebView
        pass


class _Box:
    def __init__(self, *kids) -> None:
        self.kids = kids

    def get_children(self):
        return list(self.kids)


def test_enable_finds_the_webview_and_sets_languages(monkeypatch) -> None:
    monkeypatch.setenv("LANGUAGE", "")
    monkeypatch.setenv("LC_ALL", "")
    monkeypatch.setenv("LC_MESSAGES", "")
    monkeypatch.setenv("LANG", "es_ES.UTF-8")
    view = _View()
    assert enable_linux(_Box(_Box(), _Box(view))) == ["es_ES", "en_US"]
    assert view.ctx.enabled is True
    assert enable_linux(_Box()) == []


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="WebKitGTK is Linux's")
def test_real_webkit_context_keeps_installed_language(monkeypatch) -> None:
    gi = pytest.importorskip("gi")
    for ver in ("4.1", "4.0"):
        try:
            gi.require_version("WebKit2", ver)
            break
        except ValueError:
            continue
    else:
        pytest.skip("no WebKit2 typelib")
    from gi.repository import WebKit2

    monkeypatch.setenv("LANGUAGE", "")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    ctx = WebKit2.WebContext.new_ephemeral()

    class _Real:
        def get_context(self):
            return ctx

        def load_uri(self, uri) -> None:
            pass

    assert "en_US" in enable_linux(_Real())
    assert ctx.get_spell_checking_enabled() is True
