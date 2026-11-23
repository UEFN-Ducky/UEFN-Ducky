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


def test_caption_system_menu_posts_to_owner_after_button_release(native):
    user32, sc = native
    user32.GetAsyncKeyState.return_value = 0
    assert chrome.show_native_window_menu(SimpleNamespace(hwnd=202))
    sc.PostMessageW.assert_called_once_with(202, chrome._WM_NCRBUTTONUP, chrome._HTCAPTION,
                                           ((-40 & 0xFFFF) << 16) | (-100 & 0xFFFF))
    user32.ReleaseCapture.assert_not_called()


@pytest.mark.parametrize("missing", ["window", "cursor"])
def test_caption_menu_ignores_missing_window_or_cursor(native, missing):
    user32, sc = native
    if missing == "cursor":
        user32.GetCursorPos = Mock(return_value=False)
    assert not chrome.show_native_window_menu(SimpleNamespace(hwnd=0 if missing == "window" else 202))
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
    menu = Mock()
    monkeypatch.setattr(chrome, "begin_native_window_resize", resize)
    monkeypatch.setattr(chrome, "begin_native_window_move", move)
    monkeypatch.setattr(chrome, "show_native_window_menu", menu)
    focus = object()

    chrome.install_sync_drag_bridge()
    edge.js_bridge_call(focus, "uefnNativeWindowResize", ["nw", True], "resize")
    resize.assert_called_once_with(focus, "nw", double_click=True)
    edge.js_bridge_call(focus, "uefnNativeWindowMove", [-300, 20], "move")
    move.assert_called_once_with(focus, -300, 20)
    edge.js_bridge_call(focus, "uefnNativeWindowMenu", [], "window-menu")
    menu.assert_called_once_with(focus)
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

    queued: list[tuple[object, str, str]] = []
    monkeypatch.setattr(
        ui_dispatch, "schedule_evaluate_js", lambda w, js, source="": queued.append((w, js, source))
    )
    monkeypatch.setattr(chrome.sys, "platform", "win32")
    monkeypatch.setattr(chrome.install_sync_drag_bridge, "_installed", False, raising=False)
    import webview.util

    monkeypatch.setattr(webview.util, "js_bridge_call", webview.util.js_bridge_call)

    class _Window:
        _functions: dict = {}
        _js_api = SimpleNamespace(get_version=lambda: "1.2.3")

        def evaluate_js(self, *_a, **_k):  # the blocking pywebview path — must not be hit
            threading.Event().wait()

    def _per_call_threads() -> int:
        # The call lanes' workers (call_pools, ui-call-*) are fixed and stay; any other
        # thread still alive after the reply is a per-call thread stuck on the UI.
        return sum(1 for t in threading.enumerate() if not t.name.startswith("ui-call-"))

    win = _Window()
    chrome.install_sync_drag_bridge()
    before = _per_call_threads()
    for i in range(20):
        webview.util.js_bridge_call(win, "get_version", [], f"v{i}")
    for _ in range(300):
        if len(queued) >= 20 and _per_call_threads() <= before:
            break
        threading.Event().wait(0.01)
    assert queued and queued[0][0] is win
    assert any('_returnValuesCallbacks["get_version"]["v0"]' in js for _w, js, _s in queued)
    assert "1.2.3" in queued[0][1]
    assert queued[0][2] == "get_version", "the perf log names the API call a reply answers"
    assert len(queued) == 20
    assert _per_call_threads() <= before, "pywebview's per-call thread must exit, not wait on the UI thread"
    from frontend.ui_web import call_pools

    assert sum(p.thread_count() for k, p in call_pools._pools.items() if k.startswith("ui:")) <= 14


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


def test_fit_fills_the_work_area_with_the_picture_and_its_bars():
    # 1920x1080 picture, 66 px of bars above it, a 7/14 px invisible frame, 1040 px tall work area.
    x, y, w, h = chrome.fit_picture_bounds((0, 0, 1920, 1040), (14, 7), (0, 66), (1920, 1080))
    assert h == 1040, "as tall as the work area"
    assert w - 14 == round(1920 * (1040 - 7 - 66) / 1080), "the picture keeps its shape"
    assert (x, y) == ((1920 - w) // 2, 0)
    # A wide picture on a monitor left of the primary one: limited by width, centred there.
    x, y, w, h = chrome.fit_picture_bounds((-1920, 0, 0, 1080), (0, 0), (0, 32), (3840, 1080))
    assert (w, x) == (1920, -1920)
    assert h == 32 + 540 and y == (1080 - h) // 2


@pytest.fixture
def fullscreen_native(native, monkeypatch):
    user32, _ = native
    user32.ShowWindow = Mock()
    user32.SetWindowPos = Mock()
    user32.GetWindowRect = object()
    # Maximized: the style still says so (a fake restore leaves it, the code must drop it).
    styles = {101: chrome._WS_CAPTION | chrome._WS_THICKFRAME | chrome._WS_SYSMENU | chrome._WS_MAXIMIZE}
    get_long = Mock(side_effect=lambda hwnd, _i: styles[hwnd])
    set_long = Mock(side_effect=lambda hwnd, _i, value: styles.__setitem__(hwnd, value))
    monkeypatch.setattr(chrome, "_style_long", lambda: (get_long, set_long))
    monkeypatch.setattr(chrome, "_window_rect", lambda _fn, _hwnd: (100, 80, 1380, 900))
    monkeypatch.setattr(chrome, "_monitor_rects", lambda _hwnd: ((-1920, 0, 0, 1080), (-1920, 0, 0, 1040)))
    monkeypatch.setattr(chrome, "_set_round_corners", Mock())
    monkeypatch.setattr(chrome, "_normal_size", lambda _hwnd: (1280, 820))
    page = Mock()
    monkeypatch.setattr(chrome, "_set_page_class", page)
    monkeypatch.setattr(chrome, "_fullscreen", {})
    return user32, styles, page


def test_full_screen_covers_the_whole_monitor_and_comes_back(fullscreen_native, monkeypatch):
    user32, styles, page = fullscreen_native
    win = SimpleNamespace(hwnd=101)
    monkeypatch.setattr(chrome, "_is_maximized", lambda hwnd: True)
    assert chrome.set_window_fullscreen(win, True) is True
    user32.ShowWindow.assert_called_once_with(101, chrome._SW_RESTORE)
    assert styles[101] & (chrome._WS_CAPTION | chrome._WS_THICKFRAME) == 0, "no frame: the client is the monitor"
    assert styles[101] & chrome._WS_MAXIMIZE == 0, "not 'maximized' at the monitor's size"
    assert user32.SetWindowPos.call_args.args[:6] == (101, 0, -1920, 0, 1920, 1080), "the taskbar too"
    page.assert_called_with(win, "window-fullscreen", True)
    # Asked again (the page reloaded and lost its class): nothing moves, the page hears it is.
    assert chrome.set_window_fullscreen(win, True) is True
    assert user32.SetWindowPos.call_count == 1
    assert chrome.set_window_fullscreen(win, False) is False
    assert styles[101] & chrome._WS_THICKFRAME, "the frame is back"
    assert styles[101] & chrome._WS_MAXIMIZE == 0, "Windows maximizes it again, at the right size"
    assert user32.SetWindowPos.call_args.args[:6] == (101, 0, 100, 80, 1280, 820)
    assert user32.ShowWindow.call_args.args == (101, chrome._SW_MAXIMIZE), "maximized before, maximized again"
    page.assert_called_with(win, "window-fullscreen", False)


@pytest.mark.parametrize("now,fixed", [((1248, 773), True), ((1719, 1032), False)])
def test_back_from_maximized_keeps_its_size_not_one_frame_smaller(native, monkeypatch, now, fixed):
    """WinForms thinks a frameless form has no border, so it brought the window back at
    its old client size (16x8 px smaller at 100%). Only that shrink is undone."""
    own = SimpleNamespace(GetWindowRect="outer", GetClientRect="inner", SetWindowPos=Mock())
    monkeypatch.setattr(chrome, "_user32_own", own)
    monkeypatch.setattr(chrome, "_normal_size", lambda _hwnd: (1264, 781))
    monkeypatch.setattr(chrome, "_fullscreen", {})
    rects = {"outer": (0, 0, *now), "inner": (0, 0, now[0] - 16, now[1] - 8)}
    monkeypatch.setattr(chrome, "_window_rect", lambda fn, _hwnd: rects[fn])
    form = SimpleNamespace(pywebview_window=SimpleNamespace(hwnd=101))
    chrome._keep_normal_size(form, "Normal", "Maximized", "Normal")
    chrome._keep_normal_size(form, "Maximized", "Normal", "Normal")
    if fixed:
        assert own.SetWindowPos.call_args.args[:6] == (101, None, 0, 0, 1264, 781)
    else:
        own.SetWindowPos.assert_not_called()


def test_window_chrome_runs_each_button_on_its_own_window(native, monkeypatch):
    user32, _ = native
    user32.ShowWindow = Mock()
    win = Mock(hwnd=303)
    full, fit = Mock(), Mock()
    monkeypatch.setattr(chrome, "set_window_fullscreen", full)
    monkeypatch.setattr(chrome, "fit_window_to_picture", fit)
    chrome.window_chrome(win, "minimize", [])
    chrome.window_chrome(win, "maximize", [True])
    chrome.window_chrome(win, "maximize", [False])
    chrome.window_chrome(win, "fullscreen", [True])
    chrome.window_chrome(win, "fit", [1920, 1080, 0, 66])
    chrome.window_chrome(win, "close", [])
    chrome.window_chrome(win, "evaluate_js", ["alert(1)"])
    # Straight to Windows, like the taskbar: pywebview's restore came back a frame smaller.
    assert [c.args for c in user32.ShowWindow.call_args_list] == [
        (303, chrome._SW_MINIMIZE), (303, chrome._SW_MAXIMIZE), (303, chrome._SW_RESTORE),
    ]
    win.restore.assert_not_called()
    full.assert_called_once_with(win, True)
    fit.assert_called_once_with(win, 1920.0, 1080.0, 0.0, 66.0)
    win.destroy.assert_called_once()
    win.evaluate_js.assert_not_called()


def test_bridge_runs_window_chrome_off_the_webview_callback(monkeypatch):
    import threading

    import webview.util

    original = Mock()
    edge = SimpleNamespace(js_bridge_call=original)
    monkeypatch.setitem(sys.modules, "webview.platforms.edgechromium", edge)
    monkeypatch.setattr(webview.util, "js_bridge_call", original)
    monkeypatch.setattr(chrome.sys, "platform", "win32")
    monkeypatch.setattr(chrome.install_sync_drag_bridge, "_installed", False, raising=False)
    done = threading.Event()
    seen: list = []

    def run(window, op, args):
        seen.append((window, op, args, threading.current_thread().name))
        done.set()

    monkeypatch.setattr(chrome, "window_chrome", run)
    remote = object()
    chrome.install_sync_drag_bridge()
    edge.js_bridge_call(remote, "uefnWindowChrome", ["fullscreen", True], "uefnWindowChrome")
    assert done.wait(5)
    assert seen == [(remote, "fullscreen", [True], "window-chrome")]
    edge.js_bridge_call(remote, "uefnWindowChrome", "bad", "uefnWindowChrome")
    original.assert_not_called()


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


def test_native_destroy_keeps_original_chain_until_message_returns(native, monkeypatch):
    _, sc = native
    callback = object()
    monkeypatch.setattr(chrome, "_native_subclass", {101: callback})
    chrome._window_min_track[101] = (360, 280)
    def original(*args):
        assert chrome._native_subclass[101] is callback
        assert chrome._native_subclass_orig[101] == 901
        return 27
    sc.CallWindowProcW.side_effect = original
    assert chrome._chrome_subclass_proc(101, 0x0082, 0, 0) == 27
    assert 101 not in chrome._native_subclass
    assert 101 not in chrome._native_subclass_orig
    assert 101 not in chrome._window_min_track


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
        os_api.DestroyWindow(hwnd)
    assert hwnd not in chrome._native_subclass
    assert hwnd not in chrome._native_subclass_orig
    assert hwnd not in chrome._window_min_track


# --- Linux (pywebview GTK): fake Gtk window and gi, so these run on every OS ------------


class FakeGtkWindow:
    def __init__(self, maximized: bool = False):
        self.calls: list[tuple] = []
        self.maximized = maximized

    def begin_move_drag(self, button, x, y, timestamp):
        self.calls.append(("move", button, x, y, timestamp))

    def begin_resize_drag(self, edge, button, x, y, timestamp):
        self.calls.append(("resize", edge, button, x, y, timestamp))

    def is_maximized(self):
        return self.maximized

    def maximize(self):
        self.calls.append(("maximize",))

    def unmaximize(self):
        self.calls.append(("unmaximize",))

    # focus / raise
    def set_startup_id(self, token):
        self.calls.append(("startup_id", token))

    def present_with_time(self, ts):
        self.calls.append(("present_with_time", ts))

    def present(self):
        self.calls.append(("present",))

    def get_window(self):
        return "gdk-window"


@pytest.fixture
def fake_gi(monkeypatch):
    """gi with Gdk edges, GLib.idle_add that runs at once and an X server answering time 777."""
    import types

    repo = types.ModuleType("gi.repository")
    repo.Gdk = SimpleNamespace(WindowEdge=SimpleNamespace(**{n: n for n in chrome._GDK_EDGES.values()}))
    repo.GLib = SimpleNamespace(idle_add=lambda fn: fn())
    repo.GdkX11 = SimpleNamespace(x11_get_server_time=lambda w: 777 if w == "gdk-window" else -1)
    gi = types.ModuleType("gi")
    gi.repository = repo
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repo)
    monkeypatch.setattr(sys, "platform", "linux")
    return monkeypatch


def test_linux_header_drag_starts_compositor_move(fake_gi):
    gtk = FakeGtkWindow()
    assert chrome.begin_native_window_move(SimpleNamespace(native=gtk), 640, 12) is True
    assert gtk.calls == [("move", 1, 640, 12, 0)]


def test_linux_every_edge_maps_to_a_gdk_resize(fake_gi):
    for edge, name in chrome._GDK_EDGES.items():
        gtk = FakeGtkWindow()
        w = SimpleNamespace(native=gtk)
        assert chrome.begin_native_window_resize(w, edge.upper(), screen_x=5, screen_y=6) is True
        assert gtk.calls == [("resize", name, 1, 5, 6, 0)]
    gtk = FakeGtkWindow()
    w = SimpleNamespace(native=gtk)
    assert chrome.begin_native_window_resize(w, "top") is False
    assert chrome.begin_native_window_resize(w, "n", double_click=True) is False
    assert gtk.calls == []


def test_linux_bridge_calls_dispatch(fake_gi):
    gtk = FakeGtkWindow()
    w = SimpleNamespace(native=gtk)
    assert chrome.linux_chrome_call(w, "uefnNativeWindowMove", [10, 20]) is True
    assert chrome.linux_chrome_call(w, "uefnNativeWindowResize", ["se", False, 30, 40]) is True
    assert chrome.linux_chrome_call(w, "uefnToggleMaximize", []) is True
    assert gtk.calls == [("move", 1, 10, 20, 0), ("resize", "SOUTH_EAST", 1, 30, 40, 0), ("maximize",)]
    assert chrome.linux_chrome_call(w, "list_chats", None) is False
    gtk = FakeGtkWindow(maximized=True)
    chrome.linux_chrome_call(SimpleNamespace(native=gtk), "uefnToggleMaximize", [])
    assert gtk.calls == [("unmaximize",)]


def test_linux_no_gtk_window_is_a_no_op(fake_gi):
    assert chrome.begin_native_window_move(SimpleNamespace(native=None), 1, 2) is False
    assert chrome.linux_chrome_call(SimpleNamespace(), "uefnNativeWindowMove", "junk") is True


def test_linux_sync_bridge_patches_gtks_copy(fake_gi):
    import types

    monkeypatch = fake_gi
    monkeypatch.delattr(chrome.install_sync_drag_bridge, "_installed", raising=False)
    seen: list[str] = []
    util = types.ModuleType("webview.util")
    util.js_bridge_call = lambda window, name, param, vid: seen.append(name)
    gtk_mod = types.ModuleType("webview.platforms.gtk")
    webview = types.ModuleType("webview")
    webview.util = util
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setitem(sys.modules, "webview.util", util)
    monkeypatch.setitem(sys.modules, "webview.platforms.gtk", gtk_mod)
    monkeypatch.setattr(chrome, "_js_api_func", lambda window, name: None)
    monkeypatch.setattr(chrome, "_QueuedReturn", lambda window, name: window)
    try:
        chrome.install_sync_drag_bridge()
        assert gtk_mod.js_bridge_call is util.js_bridge_call
        gtk = FakeGtkWindow()
        gtk_mod.js_bridge_call(SimpleNamespace(native=gtk), "uefnNativeWindowMove", [3, 4], "id")
        gtk_mod.js_bridge_call(SimpleNamespace(native=gtk), "pywebviewEventHandler", {}, "id")
        assert gtk.calls == [("move", 1, 3, 4, 0)]
        assert seen == ["pywebviewEventHandler"]
    finally:
        monkeypatch.delattr(chrome.install_sync_drag_bridge, "_installed", raising=False)


def test_linux_script_uses_the_panels_drag_selectors():
    """LINUX_CHROME_JS copies utils/windowDrag.ts's selectors; fail when they drift apart."""
    import re
    from pathlib import Path

    src = Path(__file__).parent / "web" / "src"
    drag = (src / "utils" / "windowDrag.ts").read_text(encoding="utf-8")
    assert re.search(r'const DRAG_ROOT =\s*"([^"]+)"', drag).group(1) in chrome.LINUX_CHROME_JS
    interactive = re.search(r'const INTERACTIVE =\s*"([^"]+)"', drag).group(1)
    for selector in interactive.split(", "):
        assert selector in chrome.LINUX_CHROME_JS
    grips = (src / "components" / "WindowResize.tsx").read_text(encoding="utf-8")
    assert "window-resize-grip--${edge}" in grips


@pytest.mark.parametrize(("env", "expected"), [("1", True), ("on", True), ("0", False), ("off", False)])
def test_linux_system_decorations_env(monkeypatch, env, expected):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("UEFN_DUCKY_SYSTEM_DECORATIONS", env)
    assert chrome.use_system_decorations() is expected


def test_system_decorations_never_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("UEFN_DUCKY_SYSTEM_DECORATIONS", "1")
    assert chrome.use_system_decorations() is False


# --- Linux focus / raise (focus_windows) -----------------------------------------------


@pytest.fixture
def raise_env(fake_gi):
    from frontend.ui_web import focus_windows

    for name in ("XDG_SESSION_TYPE", "WAYLAND_DISPLAY", "DISPLAY", "XDG_ACTIVATION_TOKEN"):
        fake_gi.delenv(name, raising=False)
    focus_windows.note_activation_token(None)
    return fake_gi, focus_windows


def test_x11_raise_uses_the_x_servers_time(raise_env):
    monkeypatch, fw = raise_env
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    gtk = FakeGtkWindow()
    fw._force_foreground(SimpleNamespace(native=gtk))
    assert gtk.calls == [("present_with_time", 777)]


def test_wayland_raise_uses_a_token_once(raise_env):
    monkeypatch, fw = raise_env
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    gtk = FakeGtkWindow()
    shown: list[str] = []
    main = SimpleNamespace(native=gtk, show=lambda: shown.append("show"), restore=lambda: shown.append("restore"))
    monkeypatch.setattr(fw, "_main_window", main)
    fw.raise_main_window("  launch-token  ")
    fw.raise_main_window()
    assert shown == ["show", "restore", "show", "restore"]
    assert gtk.calls == [("startup_id", "launch-token"), ("present",), ("present",)]


def test_notification_click_token_reaches_set_startup_id(raise_env):
    monkeypatch, fw = raise_env
    from frontend import notifications

    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    gtk = FakeGtkWindow()
    monkeypatch.setattr(fw, "_main_window", SimpleNamespace(native=gtk, show=lambda: None, restore=lambda: None))
    monkeypatch.setitem(notifications._linux_clicks, 42, notifications._raise_main_window)
    notifications.handle_linux_signal(
        "/org/freedesktop/Notifications: org.freedesktop.Notifications.ActivationToken (uint32 42, 'tok-42')")
    notifications.handle_linux_signal(
        "/org/freedesktop/Notifications: org.freedesktop.Notifications.ActionInvoked (uint32 42, 'default')")
    assert gtk.calls == [("startup_id", "tok-42"), ("present",)]
    import os

    assert "XDG_ACTIVATION_TOKEN" not in os.environ


def test_raise_without_a_window_does_nothing(raise_env):
    _, fw = raise_env
    fw._force_foreground(SimpleNamespace(native=None))
    fw.raise_main_window()
