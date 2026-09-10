"""Keep pywebview's worker notifications out of the native move/resize loop."""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from frontend.ui_web import win_frameless as chrome


@pytest.fixture
def form(monkeypatch):
    class Event:
        def __init__(self):
            self.handlers = []

        def __iadd__(self, handler):
            self.handlers.append(handler)
            return self

    class Original:
        on_resize = Mock()
        on_move = Mock()

        def __init__(self, window, cache_dir):
            self.pywebview_window = window
            self.WindowState = "normal"
            self.frameless = True
            self.HandleCreated, self.ResizeBegin, self.ResizeEnd = Event(), Event(), Event()

    platform = SimpleNamespace(BrowserView=SimpleNamespace(BrowserForm=Original), is_chromium=True)
    monkeypatch.setitem(sys.modules, "webview.platforms", SimpleNamespace(winforms=platform))
    forms = ModuleType("System.Windows.Forms")
    forms.FormWindowState = SimpleNamespace(Normal="normal", Maximized="maximized", Minimized="minimized")
    windows = ModuleType("System.Windows")
    windows.Forms = forms
    system = ModuleType("System")
    system.Windows = windows
    for name, module in [("System", system), ("System.Windows", windows), ("System.Windows.Forms", forms)]:
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(chrome, "_OriginalBrowserForm", None)
    monkeypatch.setattr(chrome, "_hwnd_from_pywebview", lambda window: 101)
    monkeypatch.setattr(chrome, "_force_frame_changed", Mock())
    monkeypatch.setattr(chrome, "reset_webview_pin", Mock())
    monkeypatch.setattr(chrome, "_pin_webview_once", Mock())
    monkeypatch.setattr(chrome, "_sync_window_chrome_state", Mock())
    instance = chrome._make_chrome_browser_form()(object(), None)
    instance.on_resize(None, None)
    Original.on_resize.reset_mock()
    chrome._sync_window_chrome_state.reset_mock()
    return instance, Original


def test_hundreds_of_resize_moves_emit_only_final_bounds_notifications(form):
    window, original = form
    assert window.ResizeBegin.handlers == [window._chrome_resize_begin]
    assert window.ResizeEnd.handlers == [window._chrome_resize_end]
    window._chrome_resize_begin(None, None)
    for _ in range(500):
        window.on_resize(None, None)
        window.on_move(None, None)
    original.on_resize.assert_not_called()
    original.on_move.assert_not_called()
    chrome._sync_window_chrome_state.assert_not_called()
    chrome.reset_webview_pin.assert_not_called()

    window._chrome_resize_end(None, None)
    original.on_resize.assert_called_once_with(window, None, None)
    original.on_move.assert_called_once_with(window, None, None)
    window._chrome_resize_end(None, None)
    assert original.on_resize.call_count == original.on_move.call_count == 1


def test_caption_drag_does_not_emit_a_spurious_resize(form):
    window, original = form
    window._chrome_resize_begin(None, None)
    for _ in range(100):
        window.on_move(None, None)
    window._chrome_resize_end(None, None)
    original.on_resize.assert_not_called()
    original.on_move.assert_called_once_with(window, None, None)


def test_programmatic_changes_still_notify_immediately(form):
    window, original = form
    window.on_resize(None, None)
    window.on_move(None, None)
    original.on_resize.assert_called_once_with(window, None, None)
    original.on_move.assert_called_once_with(window, None, None)


def test_native_snap_state_updates_immediately_and_frame_refresh_cannot_recurse(form):
    window, original = form
    window._chrome_resize_begin(None, None)
    chrome._force_frame_changed.side_effect = lambda hwnd: window.on_resize(None, None)
    window.WindowState = "maximized"
    window.on_resize(None, None)
    original.on_resize.assert_called_once_with(window, None, None)
    chrome._force_frame_changed.assert_called_once_with(101)
    chrome._sync_window_chrome_state.assert_called_once_with(window.pywebview_window)


def test_standard_windows_keep_original_notifications(form):
    window, original = form
    window.frameless = False
    window._chrome_resize_begin(None, None)
    window.on_move(None, None)
    original.on_move.assert_called_once_with(window, None, None)
