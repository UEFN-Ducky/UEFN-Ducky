"""Store EXE: native WebView2 context menu stays off."""

from __future__ import annotations

from types import SimpleNamespace

from frontend.ui_web import webview_ship as ws


def test_dev_runtime_leaves_native_menu() -> None:
    settings = SimpleNamespace(AreDefaultContextMenusEnabled=True)
    ws.apply_shipped_webview2_settings(settings)
    assert settings.AreDefaultContextMenusEnabled is True


def test_packaged_runtime_disables_native_menu(monkeypatch) -> None:
    monkeypatch.setattr(ws, "is_packaged_runtime", lambda: True)
    settings = SimpleNamespace(AreDefaultContextMenusEnabled=True)
    ws.apply_shipped_webview2_settings(settings)
    assert settings.AreDefaultContextMenusEnabled is False


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
