from __future__ import annotations

from frontend.window_view import (
    bring_to_front,
    handle_stream_message,
    kind_for,
    map_norm_to_screen,
    window_box,
    window_fit_size,
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
