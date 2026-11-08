from __future__ import annotations

import sys

import pytest

from frontend.window_view import (
    bring_to_front,
    fortnite_roots_from_launcher_dat,
    fortnite_studio_launch_from_item,
    handle_stream_message,
    kind_for,
    close_uefn,
    kill_uefn_cmd,
    launch_uefn,
    launch_uefn_cmd,
    launch_uefn_project,
    map_norm_to_screen,
    restart_uefn_project,
    uefn_editor_exe,
    uefn_editor_launch,
    uefnproject_path,
    window_box,
    window_fit_size,
    _keep_window,
    _screen_view_rows,
    _view_rect,
    _vk_for_key,
)


def test_kind_for_uefn_and_blender() -> None:
    assert kind_for("Unreal Editor for Fortnite", "UnrealEditorFortnite.exe") == "uefn"
    assert kind_for("ExampleProject1 - Unreal Editor", "UnrealEditorFortnite.exe") == "uefn"
    assert kind_for("Blender", "blender.exe") == "blender"
    assert kind_for("Notes", "notepad.exe") == "app"


def test_map_norm_to_screen_corners() -> None:
    box = (100, 200, 300, 400)
    assert map_norm_to_screen(box, 0, 0) == (100, 200)
    assert map_norm_to_screen(box, 1, 1) == (299, 399)
    assert map_norm_to_screen(box, -1, 2) == (100, 399)
    x, y = map_norm_to_screen(box, 0.5, 0.5)
    assert 100 <= x <= 299 and 200 <= y <= 399


def test_window_fit_size_clamps() -> None:
    assert window_fit_size(80, 80) == (400, 300)
    assert window_fit_size(1600, 900) == (1600, 900)


def test_window_fit_size_keeps_aspect_inside_work_area() -> None:
    # 16:9 viewer on a 1920x1040 work area: width-bound.
    w, h = window_fit_size(3840, 2160, 1920, 1040)
    assert w <= 1920 and h <= 1040
    assert abs(w / h - 16 / 9) < 0.02
    # Portrait phone (3x DPR): height-bound, never taller than the screen.
    w, h = window_fit_size(1170, 2532, 1920, 1040)
    assert h == 1040
    assert abs(w / h - 1170 / 2532) < 0.02
    assert w >= 400


def test_bring_to_front_skips_when_already_foreground(monkeypatch) -> None:
    import frontend.window_view as wv

    monkeypatch.setattr(wv.sys, "platform", "win32")
    monkeypatch.setattr(wv, "_is_our_hwnd", lambda hwnd: False)
    monkeypatch.setattr(wv, "_foreground_hwnd", lambda: 7)
    raised: list[int] = []
    monkeypatch.setattr(wv, "_raise_window", lambda hwnd: raised.append(hwnd) or True)
    assert bring_to_front(7) is True
    assert raised == []
    assert bring_to_front(8) is True
    assert raised == [8]


def test_raise_window_never_steals_focus_from_our_own_window(monkeypatch) -> None:
    """Regression: SetForegroundWindow/AttachThreadInput against the Ducky panel
    deadlocked the UI thread with WebView2 (WER AppHangXProcB1). Only the
    NOACTIVATE z-order flip may run when we are the foreground window."""
    import ctypes
    import types

    import frontend.window_view as wv

    calls: list[str] = []

    class _User32:
        def __getattr__(self, name):
            def _fn(*_a, **_k):
                calls.append(name)
                return 1 if name in ("IsWindow", "GetForegroundWindow") else 0

            return _fn

    fake = types.SimpleNamespace(user32=_User32(), kernel32=types.SimpleNamespace())
    monkeypatch.setattr(ctypes, "windll", fake, raising=False)
    monkeypatch.setattr(wv, "_send_key", lambda *a, **k: calls.append("SendInput"))

    monkeypatch.setattr(wv, "_is_our_hwnd", lambda hwnd: True)
    assert wv._raise_window(5) is True
    assert calls.count("SetWindowPos") == 2
    assert "AttachThreadInput" not in calls
    assert "SetForegroundWindow" not in calls and "SendInput" not in calls

    calls.clear()
    monkeypatch.setattr(wv, "_is_our_hwnd", lambda hwnd: False)
    assert wv._raise_window(5) is True
    assert "SetForegroundWindow" in calls
    assert "AttachThreadInput" not in calls


def test_window_box_empty_for_bad_hwnd() -> None:
    assert window_box(0) == {}


@pytest.fixture
def fake_titles(monkeypatch):
    """Our windows' titles, with the Win32 calls swapped for a dict."""
    import frontend.window_view as wv

    titles = {10: "UEFN Ducky", 11: "Remote Ducky"}

    def _set(hwnd: int, text: str) -> bool:
        titles[hwnd] = text
        return True

    monkeypatch.setattr(wv.sys, "platform", "win32")
    monkeypatch.setattr(wv, "_our_titled_windows", lambda: [(h, t) for h, t in titles.items() if t])
    monkeypatch.setattr(wv, "_window_title", lambda hwnd: titles.get(hwnd, ""))
    monkeypatch.setattr(wv, "_set_title", _set)
    monkeypatch.setattr(wv, "_shield_holds", 0)
    monkeypatch.setattr(wv, "_shield_titles", {})
    yield titles
    if wv._shield_timer is not None:
        wv._shield_timer.cancel()
        wv._shield_timer = None


def test_capture_shield_blanks_our_titles_until_the_last_hold_ends(fake_titles) -> None:
    """Oct 22 + 23 2026: Chromium's screen picker thumbnailed the Ducky window with
    PrintWindow while WebView2's UI thread waited on that thumbnail thread. Clicks died,
    the page kept animating, and the browser ran out of memory 20 minutes later. WebRTC
    never lists an untitled window, so a capture starts with our titles blank."""
    from frontend.window_view import shield_capture_list

    assert shield_capture_list(True) is True
    assert fake_titles == {10: "", 11: ""}
    assert shield_capture_list(True) is True
    shield_capture_list(False)
    assert fake_titles == {10: "", 11: ""}
    fake_titles[11] = "Renamed meanwhile"
    shield_capture_list(False)
    assert fake_titles == {10: "UEFN Ducky", 11: "Renamed meanwhile"}
    shield_capture_list(False)
    assert fake_titles == {10: "UEFN Ducky", 11: "Renamed meanwhile"}


def test_capture_shield_restores_itself_when_the_page_never_says_off(fake_titles) -> None:
    import frontend.window_view as wv

    wv.shield_capture_list(True)
    assert wv._shield_timer is not None and wv._shield_timer.interval == wv._SHIELD_MAX_S
    wv._shield_expire()
    assert fake_titles == {10: "UEFN Ducky", 11: "Remote Ducky"}
    assert wv._shield_holds == 0 and wv._shield_timer is None


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 window titles")
def test_capture_shield_on_a_real_window() -> None:
    """The real Win32 calls: a visible window of this process loses its title and gets it back."""
    import ctypes
    from ctypes import wintypes

    import frontend.window_view as wv

    user32 = ctypes.WinDLL("user32")
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    # A "Static" control as a top-level popup: visible, never activated, off-screen.
    hwnd = user32.CreateWindowExW(
        0x08000000 | 0x00000080, "Static", "Ducky shield probe", 0x80000000 | 0x10000000,
        -32000, -32000, 120, 120, None, None, None, None,
    )
    assert hwnd
    try:
        h = int(hwnd)
        assert (h, "Ducky shield probe") in wv._our_titled_windows()
        assert wv.shield_capture_list(True)
        assert wv._window_title(h) == ""
        assert all(win != h for win, _t in wv._our_titled_windows())
        assert wv.shield_capture_list(False)
        assert wv._window_title(h) == "Ducky shield probe"
    finally:
        user32.DestroyWindow(hwnd)


def test_stream_text_is_unicode_without_enter(monkeypatch) -> None:
    import frontend.window_view as wv

    raised: list[int] = []
    sent: list[list[tuple[int, int]]] = []
    keys: list[tuple[str, bool]] = []
    monkeypatch.setattr(wv, "bring_to_front", lambda hwnd: raised.append(int(hwnd)) or True)
    monkeypatch.setattr(wv, "inject_key", lambda hwnd, key, down=True: keys.append((key, down)))
    monkeypatch.setattr(wv, "_send_text", lambda text: sent.append(wv._unicode_events(text)))
    handle_stream_message(9, b'{"type":"text","text":"Hi!\\n"}')
    assert raised == [9]
    assert keys == []
    events = sent[0]
    assert [scan for scan, _flags in events] == [
        ord("H"),
        ord("H"),
        ord("i"),
        ord("i"),
        ord("!"),
        ord("!"),
    ]
    assert all(flags & 0x0004 for _scan, flags in events)
    assert all(scan != 0x0D for scan, _flags in events)


def test_look_move_sends_relative_dx(monkeypatch) -> None:
    import frontend.window_view as wv

    calls: list[tuple] = []
    monkeypatch.setattr(
        wv,
        "inject_pointer",
        lambda hwnd, kind, nx, ny, **kw: calls.append((hwnd, kind, nx, ny, kw)),
    )
    handle_stream_message(9, b'{"type":"move","dx":4,"dy":-2}')
    assert calls[0][1] == "move" and calls[0][4]["dx"] == 4
    handle_stream_message(9, b'{"type":"down","x":0.5,"y":0.5,"button":2}')
    assert calls[-1][4]["dx"] is None
    assert calls[-1][4]["button"] == 2
    handle_stream_message(9, b'{"type":"dblclick","x":0.5,"y":0.5}')
    assert calls[-1][1] == "dblclick"
    assert calls[-1][4]["dx"] is None
    handle_stream_message(9, b'{"type":"down","button":2}')
    assert calls[-1][4]["xy"] is False
    assert calls[-1][4]["button"] == 2


def test_phone_finger_is_touch_and_never_moves_the_mouse(monkeypatch) -> None:
    import frontend.window_view as wv

    touches: list[tuple[int, int, int]] = []
    mice: list[list] = []
    monkeypatch.setattr(wv, "_touch_ready", True)
    monkeypatch.setattr(wv, "_touch_at", None)
    monkeypatch.setattr(wv, "_inject_touch", lambda x, y, flags: touches.append((x, y, flags)) or True)
    monkeypatch.setattr(wv, "_send_mice", lambda events: mice.append(events))
    monkeypatch.setattr("time.sleep", lambda _s: None)
    box = (0, 0, 1000, 500)
    wv._pointer_on_box(box, "move", 0.5, 0.5, touch=True)  # no finger down: nothing at all
    wv._pointer_on_box(box, "down", 0.5, 0.5, touch=True)
    wv._pointer_on_box(box, "move", 0.6, 0.5, touch=True)
    wv._pointer_on_box(box, "up", 0.6, 0.5, touch=True)
    wv._pointer_on_box(box, "dblclick", 0.2, 0.2, touch=True)
    assert mice == []
    flags = [f for _x, _y, f in touches]
    assert flags[0] & wv._POINTER_FLAG_DOWN and flags[1] & wv._POINTER_FLAG_UPDATE
    assert flags[2] == wv._POINTER_FLAG_UP
    assert [f & wv._POINTER_FLAG_DOWN for f in flags[3:]] == [wv._POINTER_FLAG_DOWN, 0, wv._POINTER_FLAG_DOWN, 0]
    assert wv._touch_at is None


def test_refused_touch_falls_back_to_the_mouse_for_the_session(monkeypatch) -> None:
    import frontend.window_view as wv

    mice: list[list] = []
    monkeypatch.setattr(wv, "_touch_ready", True)
    monkeypatch.setattr(wv, "_touch_at", None)
    monkeypatch.setattr(wv, "_inject_touch", lambda *_a: False)
    monkeypatch.setattr(wv, "_screen_metrics", lambda: (0, 0, 1000, 500, 1000, 500))
    monkeypatch.setattr(wv, "_send_mice", lambda events: mice.append(events))
    box = (0, 0, 1000, 500)
    wv._pointer_on_box(box, "down", 0.5, 0.5, touch=True)
    wv._pointer_on_box(box, "up", 0.5, 0.5, touch=True)
    assert len(mice) == 2  # the press and its release both reach the mouse
    assert wv._touch_ready is False


def test_stream_touch_flag_reaches_the_pointer(monkeypatch) -> None:
    import frontend.window_view as wv

    calls: list[dict] = []
    monkeypatch.setattr(wv, "inject_pointer", lambda hwnd, kind, nx, ny, **kw: calls.append(kw))
    handle_stream_message(9, b'{"type":"down","x":0.5,"y":0.5,"button":0,"touch":true}')
    handle_stream_message(9, b'{"type":"down","x":0.5,"y":0.5,"button":0}')
    assert [c["touch"] for c in calls] == [True, False]


def test_inject_pointer_uses_window_box(monkeypatch) -> None:
    import frontend.window_view as wv

    monkeypatch.setattr(wv.sys, "platform", "win32")
    monkeypatch.setattr(wv, "_is_our_hwnd", lambda hwnd: False)
    monkeypatch.setattr(wv, "_window_box", lambda hwnd: (0, 0, 800, 600))
    boxes: list[object] = []
    monkeypatch.setattr(wv, "_pointer_on_box", lambda box, *_a, **_k: boxes.append(box))
    monkeypatch.setattr(wv, "bring_to_front", lambda h: True)
    wv.inject_pointer(5, "down", 0.99, 0.01)
    assert boxes == [(0, 0, 800, 600)]


def test_inject_up_does_not_raise(monkeypatch) -> None:
    import frontend.window_view as wv

    monkeypatch.setattr(wv.sys, "platform", "win32")
    monkeypatch.setattr(wv, "_is_our_hwnd", lambda hwnd: False)
    monkeypatch.setattr(wv, "_window_box", lambda hwnd: (0, 0, 800, 600))
    monkeypatch.setattr(wv, "_pointer_on_box", lambda *_a, **_k: None)
    raised: list[int] = []
    monkeypatch.setattr(wv, "bring_to_front", lambda h: raised.append(h) or True)
    wv.inject_pointer(5, "up", 0.5, 0.5)
    assert raised == []
    wv.inject_pointer(5, "down", 0.5, 0.5)
    assert raised == [5]
    wv.inject_pointer(5, "dblclick", 0.5, 0.5)
    assert raised == [5, 5]


def test_pointer_down_up_share_absolute_coords(monkeypatch) -> None:
    import frontend.window_view as wv

    events: list[tuple] = []
    monkeypatch.setattr(wv, "_send_mice", lambda ev: events.extend(ev))
    monkeypatch.setattr(wv, "_screen_metrics", lambda: (0, 0, 1920, 1080, 1920, 1080))
    box = (100, 200, 300, 400)
    wv._pointer_on_box(box, "down", 0.5, 0.5, button=0)
    wv._pointer_on_box(box, "up", 0.5, 0.5, button=0)
    assert len(events) == 2
    assert events[0][0] == events[1][0] and events[0][1] == events[1][1]
    assert events[0][3] & wv._ABS and events[1][3] & wv._ABS


def test_dblclick_is_four_events_one_position(monkeypatch) -> None:
    import frontend.window_view as wv

    events: list[tuple] = []
    monkeypatch.setattr(wv, "_send_mice", lambda ev: events.extend(ev))
    monkeypatch.setattr(wv, "_screen_metrics", lambda: (0, 0, 1920, 1080, 1920, 1080))
    wv._pointer_on_box((100, 200, 300, 400), "dblclick", 0.5, 0.5)
    assert len(events) == 4
    assert all(e[0] == events[0][0] and e[1] == events[0][1] for e in events)


def test_button_only_down_has_no_abs(monkeypatch) -> None:
    import frontend.window_view as wv

    events: list[tuple] = []
    monkeypatch.setattr(wv, "_send_mice", lambda ev: events.extend(ev))
    wv._pointer_on_box(None, "down", 0, 0, button=2)
    assert len(events) == 1
    assert events[0][3] == wv._BUTTON_DOWN[2]
    assert not (events[0][3] & wv._ABS)
    events.clear()
    wv._pointer_on_box(None, "up", 0, 0, button=2)
    assert events[0][3] == wv._BUTTON_UP[2]
    assert not (events[0][3] & wv._ABS)


def test_button_only_inject_does_not_raise(monkeypatch) -> None:
    import frontend.window_view as wv

    monkeypatch.setattr(wv.sys, "platform", "win32")
    monkeypatch.setattr(wv, "_is_our_hwnd", lambda hwnd: False)
    boxes: list[object] = []
    monkeypatch.setattr(wv, "_pointer_on_box", lambda box, *_a, **_k: boxes.append(box))
    raised: list[int] = []
    monkeypatch.setattr(wv, "bring_to_front", lambda h: raised.append(h) or True)
    wv.inject_pointer(5, "down", 0, 0, button=2, xy=False)
    assert boxes == [None]
    assert raised == []


def test_inject_key_skips_raise_on_keyup(monkeypatch) -> None:
    import frontend.window_view as wv

    monkeypatch.setattr(wv.sys, "platform", "win32")
    monkeypatch.setattr(wv, "_is_our_hwnd", lambda hwnd: False)
    monkeypatch.setattr(wv, "_send_key", lambda *_a, **_k: None)
    raised: list[int] = []
    monkeypatch.setattr(wv, "bring_to_front", lambda h: raised.append(h) or True)
    wv.inject_key(5, "z", down=False)
    assert raised == []
    wv.inject_key(5, "z", down=True)
    assert raised == [5]


def test_chord_does_not_raise_between_keys(monkeypatch) -> None:
    import frontend.window_view as wv

    monkeypatch.setattr(wv.sys, "platform", "win32")
    monkeypatch.setattr(wv, "_is_our_hwnd", lambda hwnd: False)
    monkeypatch.setattr(wv, "_send_key", lambda *_a, **_k: None)
    fg = [0]
    monkeypatch.setattr(wv, "_foreground_hwnd", lambda: fg[0])
    raised: list[int] = []

    def _raise(hwnd: int) -> bool:
        raised.append(hwnd)
        fg[0] = hwnd
        return True

    monkeypatch.setattr(wv, "_raise_window", _raise)
    wv._LAST_RAISE.clear()
    wv.inject_key(5, "Control", down=True)
    wv.inject_key(5, "z", down=True)
    assert raised == [5]


def test_vk_for_named_and_function_keys() -> None:
    assert _vk_for_key("Enter") == 0x0D
    assert _vk_for_key("F1") == 0x70
    assert _vk_for_key("F12") == 0x7B
    assert _vk_for_key("") == 0


def test_bring_to_front_has_a_cooldown(monkeypatch) -> None:
    import frontend.window_view as wv

    monkeypatch.setattr(wv.sys, "platform", "win32")
    monkeypatch.setattr(wv, "_is_our_hwnd", lambda hwnd: False)
    monkeypatch.setattr(wv, "_foreground_hwnd", lambda: 0)
    raised: list[int] = []
    monkeypatch.setattr(wv, "_raise_window", lambda hwnd: raised.append(hwnd) or True)
    wv._LAST_RAISE.clear()
    assert bring_to_front(9) is True
    assert bring_to_front(9) is False, "second raise inside the cooldown is skipped"
    assert raised == [9]
    wv._LAST_RAISE[9] -= wv.RAISE_COOLDOWN_S + 1
    assert bring_to_front(9) is True
    assert raised == [9, 9]


def test_keep_window_drops_shell_junk() -> None:
    keep = dict(owned=False, toolwindow=False, cloaked=False, iconic=False, width=800, height=600)
    assert not _keep_window(title="", **keep)
    assert not _keep_window(title="Notes", owned=True, toolwindow=False, cloaked=False, iconic=False, width=800, height=600)
    assert not _keep_window(title="Notes", owned=False, toolwindow=True, cloaked=False, iconic=False, width=800, height=600)
    assert not _keep_window(title="Notes", owned=False, toolwindow=False, cloaked=True, iconic=False, width=800, height=600)
    assert not _keep_window(title="Notes", owned=False, toolwindow=False, cloaked=False, iconic=False, width=1, height=1)
    assert not _keep_window(title="Program Manager", **keep)
    assert not _keep_window(title="DWM Notification Window", **keep)
    assert _keep_window(title="Documents", **keep)
    assert _keep_window(title="Notes", owned=False, toolwindow=False, cloaked=False, iconic=True, width=0, height=0)


def test_screen_view_rows_single_and_multi() -> None:
    assert _screen_view_rows([(0, 0, 1920, 1080)]) == [
        {"id": "desktop", "title": "Entire desktop", "kind": "desktop"}
    ]
    rows = _screen_view_rows([(0, 0, 3840, 1080), (3840, 0, 1920, 1080)])
    assert [r["id"] for r in rows] == ["desktop", "monitor:0", "monitor:1"]
    assert rows[2]["title"] == "Display 2 (1920\u00d71080)"


def test_view_rect_desktop_and_monitor() -> None:
    desktop = (0, 0, 5760, 1080)
    mons = [(0, 0, 3840, 1080), (3840, 0, 1920, 1080)]
    assert _view_rect("desktop", desktop=desktop, monitors=mons) == (0, 0, 5760, 1080)
    assert _view_rect("monitor:1", desktop=desktop, monitors=mons) == (3840, 0, 5760, 1080)
    assert _view_rect("monitor:9", desktop=desktop, monitors=mons) is None


def test_window_box_desktop_and_monitor(monkeypatch) -> None:
    import frontend.window_view as wv

    monkeypatch.setattr(wv.sys, "platform", "win32")
    monkeypatch.setattr(wv, "_screen_metrics", lambda: (0, 0, 5760, 1080, 3840, 1080))
    monkeypatch.setattr(wv, "_monitors", lambda: [(0, 0, 3840, 1080), (3840, 0, 1920, 1080)])
    desk = window_box("desktop")
    assert desk["left"] == 0 and desk["right"] == 5760 and desk["screen_w"] == 5760
    mon = window_box("monitor:1")
    assert mon["left"] == 3840 and mon["right"] == 5760 and mon["bottom"] == 1080
    assert window_box("monitor:9") == {}


def test_desktop_size_does_not_fit(monkeypatch) -> None:
    import frontend.window_view as wv

    calls: list[tuple] = []
    monkeypatch.setattr(wv, "fit_window", lambda *a: calls.append(a))
    handle_stream_message("desktop", b'{"type":"size","w":800,"h":600}')
    handle_stream_message("monitor:0", b'{"type":"size","w":800,"h":600}')
    assert calls == []


def test_desktop_pointer_skips_raise(monkeypatch) -> None:
    import frontend.window_view as wv

    monkeypatch.setattr(wv.sys, "platform", "win32")
    monkeypatch.setattr(wv, "_screen_metrics", lambda: (0, 0, 5760, 1080, 3840, 1080))
    monkeypatch.setattr(wv, "_monitors", lambda: [(0, 0, 3840, 1080), (3840, 0, 1920, 1080)])
    boxes: list[object] = []
    monkeypatch.setattr(wv, "_pointer_on_box", lambda box, *_a, **_k: boxes.append(box))
    raised: list[int] = []
    monkeypatch.setattr(wv, "bring_to_front", lambda h: raised.append(h))
    handle_stream_message("desktop", b'{"type":"down","x":0.5,"y":0.5}')
    assert boxes == [(0, 0, 5760, 1080)]
    assert raised == []


def test_trackpad_scroll_and_buttons_leave_the_cursor_where_it_is(monkeypatch) -> None:
    import frontend.window_view as wv

    monkeypatch.setattr(wv.sys, "platform", "win32")
    sent: list[list[tuple]] = []
    monkeypatch.setattr(wv, "_send_mice", sent.append)
    handle_stream_message("desktop", b'{"type":"wheel","delta":-2}')
    handle_stream_message("desktop", b'{"type":"move","dx":5,"dy":-3}')
    handle_stream_message("desktop", b'{"type":"down","button":2}')
    assert sent == [[(0, 0, -240, 0x0800)], [(5, -3, 0, 0x0001)], [(0, 0, 0, 0x0008)]]


def test_uefnproject_path_finds_file(tmp_path) -> None:
    island = tmp_path / "Island.uefnproject"
    island.write_text("{}", encoding="utf-8")
    assert uefnproject_path(tmp_path) == island
    assert uefnproject_path(island) == island


def test_uefnproject_path_missing_root(tmp_path) -> None:
    try:
        uefnproject_path(tmp_path)
    except RuntimeError as e:
        assert ".uefnproject" in str(e)
    else:
        raise AssertionError("expected missing .uefnproject")


def test_uefnproject_path_no_settings(monkeypatch) -> None:
    class _S:
        uefn_project_root = ""

        @classmethod
        def load(cls):
            return cls()

    monkeypatch.setattr("frontend.settings.PanelSettings", _S)
    try:
        uefnproject_path()
    except RuntimeError as e:
        assert "No project selected" in str(e)
    else:
        raise AssertionError("expected no project")


def test_kill_uefn_cmd_is_editor_only() -> None:
    cmd = kill_uefn_cmd()
    assert cmd == [
        "taskkill",
        "/IM",
        "UnrealEditorFortnite-Win64-Shipping.exe",
        "/IM",
        "UnrealEditorFortnite.exe",
        "/F",
    ]
    assert "UEFN-Ducky" not in " ".join(cmd)


def test_launch_uefn_cmd_goes_through_editor() -> None:
    exe = r"C:\Epic\Fortnite\Engine\Binaries\Win64\UnrealEditorFortnite.exe"
    island = r"C:\islands\Demo.uefnproject"
    assert launch_uefn_cmd(exe, island) == [exe, f"-ValkyrieProject={island}"]
    assert launch_uefn_cmd(exe) == [exe]
    extra = ["-obfuscationid=abc"]
    shipping = r"C:\Epic\Fortnite\FortniteGame\Binaries\Win64\UnrealEditorFortnite-Win64-Shipping.exe"
    assert launch_uefn_cmd(shipping, island, extra) == [
        shipping,
        extra[0],
        f"-ValkyrieProject={island}",
    ]


def test_fortnite_roots_from_launcher_dat() -> None:
    raw = """{"InstallationList":[
      {"InstallLocation":"C:\\\\Epic\\\\Fortnite","AppName":"Fortnite","ArtifactId":"Fortnite"},
      {"InstallLocation":"C:\\\\Epic\\\\UE","AppName":"UE_5"}
    ]}"""
    assert fortnite_roots_from_launcher_dat(raw) == [r"C:\Epic\Fortnite"]
    assert fortnite_roots_from_launcher_dat("not-json") == []


def test_fortnite_studio_launch_from_item() -> None:
    raw = """{
      "AppName": "Fortnite_Studio",
      "DisplayName": "Unreal Editor for Fortnite",
      "InstallLocation": "C:\\\\Epic\\\\Fortnite",
      "LaunchExecutable": "FortniteGame/Binaries/Win64/UnrealEditorFortnite-Win64-Shipping.exe",
      "LaunchCommand": " -obfuscationid=abc"
    }"""
    exe, extra = fortnite_studio_launch_from_item(raw) or ("", [])
    assert exe.replace("/", "\\").endswith(
        "Fortnite\\FortniteGame\\Binaries\\Win64\\UnrealEditorFortnite-Win64-Shipping.exe"
    )
    assert extra == ["-obfuscationid=abc"]
    assert fortnite_studio_launch_from_item(
        '{"AppName":"Fortnite","InstallLocation":"C:\\\\F",'
        '"LaunchExecutable":"FortniteGame/Binaries/Win64/FortniteClient-Win64-Shipping.exe"}'
    ) is None
    assert fortnite_studio_launch_from_item("not-json") is None


def _touch_editor(root, *parts: str):
    path = root.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_uefn_editor_launch_prefers_studio_item(tmp_path, monkeypatch) -> None:
    import json

    pd = tmp_path / "ProgramData"
    manifests = pd / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    manifests.mkdir(parents=True)
    root = tmp_path / "Fortnite"
    exe = _touch_editor(
        root,
        "FortniteGame",
        "Binaries",
        "Win64",
        "UnrealEditorFortnite-Win64-Shipping.exe",
    )
    (manifests / "studio.item").write_text(
        json.dumps(
            {
                "AppName": "Fortnite_Studio",
                "DisplayName": "Unreal Editor for Fortnite",
                "InstallLocation": str(root),
                "LaunchExecutable": "FortniteGame/Binaries/Win64/UnrealEditorFortnite-Win64-Shipping.exe",
                "LaunchCommand": " -obfuscationid=abc",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PROGRAMDATA", str(pd))
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "pf"))
    found, extra = uefn_editor_launch()
    assert found == str(exe)
    assert extra == ["-obfuscationid=abc"]
    assert uefn_editor_exe() == str(exe)


def test_uefn_editor_launch_shipping_on_root(tmp_path, monkeypatch) -> None:
    pf = tmp_path / "pf"
    exe = _touch_editor(
        pf / "Epic Games" / "Fortnite",
        "FortniteGame",
        "Binaries",
        "Win64",
        "UnrealEditorFortnite-Win64-Shipping.exe",
    )
    monkeypatch.setenv("PROGRAMFILES", str(pf))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "empty-pd"))
    assert uefn_editor_exe() == str(exe)


def test_uefn_editor_launch_legacy_engine_exe(tmp_path, monkeypatch) -> None:
    pf = tmp_path / "pf"
    exe = _touch_editor(
        pf / "Epic Games" / "Fortnite",
        "Engine",
        "Binaries",
        "Win64",
        "UnrealEditorFortnite.exe",
    )
    monkeypatch.setenv("PROGRAMFILES", str(pf))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "empty-pd"))
    assert uefn_editor_exe() == str(exe)


def test_uefn_editor_launch_shipping_beats_legacy(tmp_path, monkeypatch) -> None:
    pf = tmp_path / "pf"
    root = pf / "Epic Games" / "Fortnite"
    shipping = _touch_editor(
        root,
        "FortniteGame",
        "Binaries",
        "Win64",
        "UnrealEditorFortnite-Win64-Shipping.exe",
    )
    _touch_editor(root, "Engine", "Binaries", "Win64", "UnrealEditorFortnite.exe")
    monkeypatch.setenv("PROGRAMFILES", str(pf))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "empty-pd"))
    assert uefn_editor_exe() == str(shipping)


def test_launch_uefn_project_uses_editor(tmp_path, monkeypatch) -> None:
    import frontend.window_view as wv

    island = tmp_path / "Demo.uefnproject"
    island.write_text("{}", encoding="utf-8")
    exe = r"C:\Epic\Fortnite\FortniteGame\Binaries\Win64\UnrealEditorFortnite-Win64-Shipping.exe"
    extra = ["-obfuscationid=abc"]
    started: list[tuple] = []
    monkeypatch.setattr(wv, "uefn_editor_launch", lambda: (exe, extra))
    monkeypatch.setattr(wv, "uefnproject_path", lambda: island)
    monkeypatch.setattr(
        wv,
        "_start_uefn",
        lambda editor, project=None, extra=None: started.append(
            (editor, str(project) if project else "", extra)
        ),
    )
    assert launch_uefn_project() == {"ok": True, "exe": exe, "path": str(island)}
    assert started == [(exe, str(island), extra)]


def test_launch_uefn_hub_skips_project(monkeypatch) -> None:
    import frontend.window_view as wv

    exe = r"C:\Epic\Fortnite\FortniteGame\Binaries\Win64\UnrealEditorFortnite-Win64-Shipping.exe"
    extra = ["-obfuscationid=abc"]
    started: list[tuple] = []
    monkeypatch.setattr(wv, "uefn_editor_launch", lambda: (exe, extra))
    monkeypatch.setattr(
        wv,
        "_start_uefn",
        lambda editor, project=None, extra=None: started.append((editor, project, extra)),
    )
    assert launch_uefn() == {"ok": True, "exe": exe, "path": ""}
    assert started == [(exe, None, extra)]


def test_close_uefn_kills_when_no_window(monkeypatch) -> None:
    import frontend.window_view as wv

    monkeypatch.setattr(wv, "_request_uefn_close", lambda: 0)
    monkeypatch.setattr(wv, "_kill_uefn_editor", lambda: True)
    monkeypatch.setattr(
        wv,
        "_start_uefn",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("close must not launch")),
    )
    assert close_uefn() == {"ok": True, "graceful": False, "killed": True}


def test_close_uefn_graceful_skips_kill(monkeypatch) -> None:
    import frontend.window_view as wv

    monkeypatch.setattr(wv, "_request_uefn_close", lambda: 1)
    monkeypatch.setattr(wv, "_wait_uefn_exit", lambda _timeout: True)
    monkeypatch.setattr(
        wv,
        "_kill_uefn_editor",
        lambda: (_ for _ in ()).throw(AssertionError("taskkill after a clean close")),
    )
    assert close_uefn() == {"ok": True, "graceful": True, "killed": False}


def test_restart_uefn_project_kills_then_launches(tmp_path, monkeypatch) -> None:
    import frontend.window_view as wv

    island = tmp_path / "Demo.uefnproject"
    island.write_text("{}", encoding="utf-8")
    exe = r"C:\Epic\Fortnite\FortniteGame\Binaries\Win64\UnrealEditorFortnite-Win64-Shipping.exe"
    order: list[str] = []
    monkeypatch.setattr(wv, "_request_uefn_close", lambda: 0)
    monkeypatch.setattr(wv, "_kill_uefn_editor", lambda: order.append("kill") or True)
    monkeypatch.setattr(wv, "uefn_editor_launch", lambda: (exe, []))
    monkeypatch.setattr(wv, "uefnproject_path", lambda: island)
    monkeypatch.setattr(
        wv,
        "_start_uefn",
        lambda editor, project=None, extra=None: order.append(f"start:{editor}:{project}"),
    )
    out = restart_uefn_project()
    assert out["ok"] is True and out["killed"] is True
    assert order == ["kill", f"start:{exe}:{island}"]


def test_wait_uefn_ready_times_out(monkeypatch) -> None:
    import frontend.window_view as wv

    monkeypatch.setattr(
        "backend.bridge.client.listener_get_health",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr("time.sleep", lambda _s: None)
    out = wv.wait_uefn_ready(timeout=1.0)
    assert out["ok"] is False
    assert "timed out" in out["error"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows monitors")
def test_monitors_survive_another_modules_monitorinfo_argtypes():
    """win_frameless sets the shared user32's GetMonitorInfoW argtypes to its own struct
    when a window maximizes; the screen list read the same shared function and fell
    back to one 1920x1080 screen."""
    import ctypes
    from ctypes import wintypes

    import frontend.window_view as wv

    class OtherInfo(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", ctypes.c_ulong)]

    shared = ctypes.windll.user32.GetMonitorInfoW
    before = shared.argtypes
    shared.argtypes = [wintypes.HMONITOR, ctypes.POINTER(OtherInfo)]
    try:
        width = ctypes.windll.user32.GetSystemMetrics(0)  # SM_CXSCREEN: the primary's width
        assert wv._monitors()[0][2] == width
    finally:
        shared.argtypes = before
