"""A Windows logoff/shutdown (WM_ENDSESSION) clears the running flag, so it isn't a crash."""

from __future__ import annotations

import sys

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="window proc is Windows-only")
def test_end_session_true_calls_app_closing_and_false_does_not(monkeypatch) -> None:
    # Oct 29 2026: a logoff with Ducky open was counted as app_unclean_exit.
    from frontend import usage_stats
    from frontend.ui_web import win_frameless as wf

    closed: list[int] = []
    monkeypatch.setattr(usage_stats, "app_closing", lambda: closed.append(1))

    class _User32:
        @staticmethod
        def DefWindowProcW(hwnd, msg, wparam, lparam):
            return 0

        CallWindowProcW = None

    monkeypatch.setattr(wf, "_user32_sc", _User32)
    monkeypatch.setattr(wf, "_native_subclass_orig", {})
    wf._chrome_subclass_proc(1, wf._WM_ENDSESSION, 0, 0)
    assert closed == []
    wf._chrome_subclass_proc(1, wf._WM_ENDSESSION, 1, 0)
    assert closed == [1]
