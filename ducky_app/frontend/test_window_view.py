from __future__ import annotations

from frontend.window_view import (
    bring_to_front,
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


def test_window_box_empty_for_bad_hwnd() -> None:
    assert window_box(0) == {}


def test_vk_for_named_and_function_keys() -> None:
    assert _vk_for_key("Enter") == 0x0D
    assert _vk_for_key("F1") == 0x70
    assert _vk_for_key("F12") == 0x7B
    assert _vk_for_key("") == 0
