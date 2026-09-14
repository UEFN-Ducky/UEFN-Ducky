"""open_external_url: remote viewers get a push, not a Windows browser."""

from __future__ import annotations

from frontend.ui_web import panel_api as _panel_api  # noqa: F401 — break mixin circular import
from frontend.ui_web.panel_api_window import PanelApiWindowMixin


def test_open_external_url_pushes_to_remote_viewer(monkeypatch) -> None:
    pushed: list[dict[str, str]] = []
    opened: list[str] = []
    api = PanelApiWindowMixin()
    api._push = lambda ev: pushed.append(ev)  # type: ignore[method-assign]
    monkeypatch.setattr("frontend.ui_web.panel_httpd.remote_session_count", lambda: 1)
    monkeypatch.setattr("webbrowser.open", lambda u: opened.append(u))
    api.open_external_url("https://example.com/docs")
    assert pushed == [{"type": "open_url", "url": "https://example.com/docs"}]
    assert opened == []


def test_open_external_url_local_uses_browser(monkeypatch) -> None:
    import webbrowser

    pushed: list[object] = []
    opened: list[str] = []
    api = PanelApiWindowMixin()
    api._push = lambda ev: pushed.append(ev)  # type: ignore[method-assign]
    monkeypatch.setattr("frontend.ui_web.panel_httpd.remote_session_count", lambda: 0)
    monkeypatch.setattr(webbrowser, "open", lambda u: opened.append(u))
    api.open_external_url("https://example.com/docs")
    assert pushed == []
    assert opened == ["https://example.com/docs"]


def test_open_external_url_rejects_http(monkeypatch) -> None:
    pushed: list[object] = []
    opened: list[str] = []
    api = PanelApiWindowMixin()
    api._push = lambda ev: pushed.append(ev)  # type: ignore[method-assign]
    monkeypatch.setattr("frontend.ui_web.panel_httpd.remote_session_count", lambda: 1)
    api.open_external_url("http://example.com")
    api.open_external_url("javascript:alert(1)")
    assert pushed == []
    assert opened == []
