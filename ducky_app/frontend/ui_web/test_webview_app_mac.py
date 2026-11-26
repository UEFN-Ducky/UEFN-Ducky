"""macOS: the main window runs on pywebview's Cocoa backend (WKWebView)."""

from __future__ import annotations

import sys
import types

from frontend import platform_info
from frontend.ui_web import webview_app as app
from frontend.ui_web import webview_recover


def _platform(monkeypatch, name: str) -> None:
    monkeypatch.setattr(sys, "platform", name)
    monkeypatch.setattr(platform_info, "IS_WINDOWS", name == "win32")
    monkeypatch.setattr(platform_info, "IS_LINUX", name.startswith("linux"))


def test_each_os_gets_its_backend(monkeypatch):
    _platform(monkeypatch, "darwin")
    assert app.webview_gui() == {"gui": "cocoa"}
    _platform(monkeypatch, "linux")
    assert app.webview_gui() == {"gui": "gtk"}
    _platform(monkeypatch, "win32")
    assert app.webview_gui() == {}


def test_mac_reloads_the_page_with_script(monkeypatch):
    _platform(monkeypatch, "darwin")
    ran: list[str] = []
    webview_recover.reload_main_page(types.SimpleNamespace(run_js=ran.append))
    assert ran == ["location.reload()"]


def test_mac_skips_the_winforms_nudge(monkeypatch):
    _platform(monkeypatch, "darwin")
    native = types.SimpleNamespace(webview=None)
    webview_recover.nudge_webview_visible(types.SimpleNamespace(native=native))  # no raise
