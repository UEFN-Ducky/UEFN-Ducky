"""The panel's own screen capture is allowed without WebView2's Screen Sharing prompt."""

from __future__ import annotations

from frontend.ui_web.win_frameless import _auto_allow_permission


def test_screen_capture_allowed_only_for_the_local_panel() -> None:
    assert _auto_allow_permission(0, "Unknown", "http://127.0.0.1:4199/")
    assert _auto_allow_permission(0, "Unknown", "http://localhost:4199/")
    assert not _auto_allow_permission(0, "Unknown", "https://example.com/")
    assert _auto_allow_permission(12, "WindowManagement", "http://127.0.0.1:4199/")
    assert not _auto_allow_permission(4, "Notifications", "https://example.com/")


def test_microphone_still_allowed() -> None:
    assert _auto_allow_permission(1, "Microphone", "https://example.com/")
