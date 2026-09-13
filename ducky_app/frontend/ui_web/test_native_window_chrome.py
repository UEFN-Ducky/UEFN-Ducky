"""Regression coverage for native chrome shared by main and focus windows."""

import ctypes
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from frontend.ui_web import win_frameless as chrome


@pytest.fixture
def native(monkeypatch):
    monkeypatch.setattr(chrome.sys, "platform", "win32")
    monkeypatch.setattr(chrome, "_hwnd_from_pywebview", lambda window: window.hwnd)
    monkeypatch.setattr(chrome, "_is_maximized", lambda hwnd: False)
    monkeypatch.setattr(chrome, "_window_min_track", {})
    monkeypatch.setattr(chrome, "_native_subclass_orig", {101: 901})
    user32 = SimpleNamespace(GetAsyncKeyState=Mock(return_value=0x8000), ReleaseCapture=Mock())

    def cursor(ptr):
        pt = ctypes.cast(ptr, ctypes.POINTER(ctypes.wintypes.POINT)).contents
        pt.x, pt.y = -100, -40
        return True

    user32.GetCursorPos = cursor
    monkeypatch.setattr(chrome, "user32", user32)
    sc = SimpleNamespace(PostMessageW=Mock(return_value=1), CallWindowProcW=Mock(return_value=0),
                         DefWindowProcW=Mock(return_value=0))
    monkeypatch.setattr(chrome, "_user32_sc", sc, raising=False)
    return user32, sc


@pytest.mark.parametrize("edge,hit", [("w", 10), ("e", 11), ("n", 12), ("nw", 13),
                                       ("ne", 14), ("s", 15), ("sw", 16), ("se", 17)])
def test_every_edge_enters_native_resize_with_physical_signed_coordinates(native, edge, hit):
    user32, sc = native
    assert chrome.begin_native_window_resize(SimpleNamespace(hwnd=202), edge)
    sc.PostMessageW.assert_called_once_with(202, chrome._WM_NCLBUTTONDOWN, hit,
                                           ((-40 & 0xFFFF) << 16) | (-100 & 0xFFFF))
    user32.ReleaseCapture.assert_called_once()


def test_border_double_click_is_native(native):
    _, sc = native
    assert chrome.begin_native_window_resize(SimpleNamespace(hwnd=101), "n", double_click=True)
    assert sc.PostMessageW.call_args.args[1:3] == (chrome._WM_NCLBUTTONDBLCLK, chrome._HTTOP)


def test_caption_drag_posts_to_owner_using_os_coordinates(native):
    _, sc = native
    assert chrome.begin_native_window_move(SimpleNamespace(hwnd=202), -50, -20)
    sc.PostMessageW.assert_called_once_with(202, chrome._WM_NCLBUTTONDOWN, chrome._HTCAPTION,
                                           ((-40 & 0xFFFF) << 16) | (-100 & 0xFFFF))


def test_late_caption_drag_does_not_stick_to_cursor(native):
    user32, sc = native
    user32.GetAsyncKeyState.return_value = 0
    assert not chrome.begin_native_window_move(SimpleNamespace(hwnd=202))
    sc.PostMessageW.assert_not_called()


@pytest.mark.parametrize("reason", ["maximized", "released", "invalid"])
def test_resize_does_not_start_after_release_or_when_maximized(native, monkeypatch, reason):
    user32, sc = native
    if reason == "maximized":
        monkeypatch.setattr(chrome, "_is_maximized", lambda hwnd: True)
    elif reason == "released":
        user32.GetAsyncKeyState.return_value = 0
    assert not chrome.begin_native_window_resize(SimpleNamespace(hwnd=101), "bad" if reason == "invalid" else "n")
    sc.PostMessageW.assert_not_called()


def test_bridge_patches_edgechromiums_import_and_preserves_origin_window(monkeypatch):
    import webview.util

    original = Mock()
    edge = SimpleNamespace(js_bridge_call=original)
    monkeypatch.setitem(sys.modules, "webview.platforms.edgechromium", edge)
    monkeypatch.setattr(webview.util, "js_bridge_call", original)
    monkeypatch.setattr(chrome.sys, "platform", "win32")
    monkeypatch.setattr(chrome.install_sync_drag_bridge, "_installed", False, raising=False)
    resize = Mock()
    move = Mock()
    monkeypatch.setattr(chrome, "begin_native_window_resize", resize)
    monkeypatch.setattr(chrome, "begin_native_window_move", move)
    focus = object()

    chrome.install_sync_drag_bridge()
    edge.js_bridge_call(focus, "uefnNativeWindowResize", ["nw", True], "resize")
    resize.assert_called_once_with(focus, "nw", double_click=True)
    edge.js_bridge_call(focus, "uefnNativeWindowMove", [-300, 20], "move")
    move.assert_called_once_with(focus, -300, 20)
    edge.js_bridge_call(focus, "get_version", [], "ordinary")
    original.assert_called_once()
    wrapped, name, args, vid = original.call_args.args
    assert (name, args, vid) == ("get_version", [], "ordinary")
    assert wrapped._w is focus, "pywebview still sees the real window's attributes"
    chrome.install_sync_drag_bridge()
    assert edge.js_bridge_call is webview.util.js_bridge_call


def test_bridge_return_value_never_blocks_the_api_call_thread(monkeypatch):
    """Regression for the 5000-thread pile-up: pywebview's per-call thread used
    to sit in window.evaluate_js (Invoke + semaphore, no timeout) once the UI
    thread stopped pumping. Returns go through ui_dispatch now."""
    import threading

    from frontend.ui_web import ui_dispatch

    queued: list[tuple[object, str]] = []
    monkeypatch.setattr(ui_dispatch, "schedule_evaluate_js", lambda w, js: queued.append((w, js)))
    monkeypatch.setattr(chrome.sys, "platform", "win32")
    monkeypatch.setattr(chrome.install_sync_drag_bridge, "_installed", False, raising=False)
    import webview.util

    monkeypatch.setattr(webview.util, "js_bridge_call", webview.util.js_bridge_call)

    class _Window:
        _functions: dict = {}
        _js_api = SimpleNamespace(get_version=lambda: "1.2.3")

        def evaluate_js(self, *_a, **_k):  # the blocking pywebview path — must not be hit
            threading.Event().wait()

    win = _Window()
    chrome.install_sync_drag_bridge()
    before = threading.active_count()
    webview.util.js_bridge_call(win, "get_version", [], "v1")
    for _ in range(300):
        if queued and threading.active_count() <= before:
            break
        threading.Event().wait(0.01)
    assert queued and queued[0][0] is win
    assert '_returnValuesCallbacks["get_version"]["v1"]' in queued[0][1]
    assert "1.2.3" in queued[0][1]
    assert threading.active_count() <= before, "pywebview's per-call thread must exit, not wait on the UI thread"


def test_minimums_are_per_window_and_scaled_to_current_monitor(native, monkeypatch):
    chrome._window_min_track.update({101: (280, 280), 202: (360, 280)})
    monkeypatch.setattr(chrome, "_hwnd_scale", lambda hwnd: 1.5 if hwnd == 101 else 2.0)
    assert chrome._current_min_track(101) == (420, 420)
    assert chrome._current_min_track(202) == (720, 560)
    assert chrome._current_min_track(303) is None


def test_exit_compact_mode_keeps_chrome_and_other_window_limits(native, monkeypatch):
    window = SimpleNamespace(hwnd=101)
    chrome._window_min_track.update({101: (280, 280), 202: (360, 280)})
    monkeypatch.setattr(chrome, "_install_native_chrome_subclass", Mock())
    monkeypatch.setattr(chrome, "_force_frame_changed", Mock())
    monkeypatch.setattr(chrome, "_apply_winforms_minimum_size", Mock())
    release = Mock()
    monkeypatch.setattr(chrome, "release_native_chrome_subclass", release)
    assert chrome.clear_window_min_track_size(window)
    assert chrome._window_min_track == {101: (700, 480), 202: (360, 280)}
    assert window.min_size == (700, 480)
    release.assert_not_called()


@pytest.mark.parametrize("extended", [False, True])
def test_caption_removed_without_losing_native_side_and_bottom_frame(native, extended):
    _, sc = native

    def original(_proc, _hwnd, _msg, _wparam, lparam):
        rect = chrome._RECT.from_address(lparam)
        rect.left += 8
        rect.top += 31
        rect.right -= 8
        rect.bottom -= 8
        return 0

    sc.CallWindowProcW.side_effect = original
    data = chrome._NCCALCSIZE_PARAMS() if extended else chrome._RECT()
    rect = data.rgrc[0] if extended else data
    rect.left, rect.top, rect.right, rect.bottom = -900, -500, -100, 100
    assert chrome._chrome_subclass_proc(101, chrome._WM_NCCALCSIZE, int(extended), ctypes.addressof(data)) == 0
    assert (rect.left, rect.top, rect.right, rect.bottom) == (-892, -500, -108, 92)


def test_maximized_client_matches_work_area_on_secondary_monitor(native):
    user32, sc = native
    user32.MonitorFromRect = Mock(return_value=5)

    def monitor_info(_hmon, ptr):
        info = ctypes.cast(ptr, ctypes.POINTER(chrome._MONITORINFO)).contents
        info.rcWork = chrome._RECT(-1920, -1040, 0, 0)
        return True

    user32.GetMonitorInfoW = Mock(side_effect=monitor_info)
    rect = chrome._RECT(-1928, -1088, 8, 8)
    assert chrome._set_nccalcsize_maximized_work_area(rect, 101)
    assert (rect.left, rect.top, rect.right, rect.bottom) == (-1920, -1040, 0, 0)
    assert user32.MonitorFromRect.call_args.args[1] == 2
    sc.CallWindowProcW.assert_not_called()


def test_maximized_grips_state_targets_own_window(native, monkeypatch):
    from frontend.ui_web import ui_dispatch

    schedule = Mock()
    monkeypatch.setattr(ui_dispatch, "schedule_evaluate_js", schedule)
    monkeypatch.setattr(chrome, "_is_maximized", lambda hwnd: hwnd == 202)
    main, focus = SimpleNamespace(hwnd=101), SimpleNamespace(hwnd=202)
    chrome._sync_window_chrome_state(main)
    chrome._sync_window_chrome_state(focus)
    assert schedule.call_args_list[0].args == (main, 'document.documentElement.classList.toggle("window-maximized", false);')
    assert schedule.call_args_list[1].args == (focus, 'document.documentElement.classList.toggle("window-maximized", true);')


@pytest.mark.skipif(sys.platform != "win32", reason="real Win32 frame smoke test")
def test_real_hidden_hwnd_has_no_top_gap_and_retains_native_resize_frame():
    """Exercise actual non-client calculations without opening or moving the app."""
    from ctypes import wintypes

    os_api = ctypes.WinDLL("user32", use_last_error=True)
    os_api.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                      wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                      wintypes.HINSTANCE, ctypes.c_void_p]
    os_api.CreateWindowExW.restype = wintypes.HWND
    os_api.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(chrome._RECT)]
    os_api.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(chrome._RECT)]
    os_api.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    os_api.DestroyWindow.argtypes = [wintypes.HWND]
    hwnd = os_api.CreateWindowExW(0, "STATIC", "Ducky chrome test", 0, 100, 100, 800, 600,
                                  None, None, None, None)
    assert hwnd, ctypes.get_last_error()
    window = SimpleNamespace(native=SimpleNamespace(Handle=SimpleNamespace(ToInt32=lambda: hwnd)),
                             min_size=(360, 280))
    try:
        assert chrome.refresh_window_chrome(window)
        outer, client, origin = chrome._RECT(), chrome._RECT(), wintypes.POINT()
        assert os_api.GetWindowRect(hwnd, ctypes.byref(outer))
        assert os_api.GetClientRect(hwnd, ctypes.byref(client))
        assert os_api.ClientToScreen(hwnd, ctypes.byref(origin))
        assert origin.y == outer.top
        assert origin.x > outer.left
        assert origin.x + client.right < outer.right
        assert origin.y + client.bottom < outer.bottom
    finally:
        chrome.release_native_chrome_subclass(hwnd)
        os_api.DestroyWindow(hwnd)
