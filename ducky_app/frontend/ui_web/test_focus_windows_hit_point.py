"""focus_windows — drop hit points; close_this_window closes the CALLER, not the active window."""

from __future__ import annotations

from frontend.ui_web import focus_windows
from frontend.ui_web.focus_windows import drop_hit_points


def test_drop_point_is_tried_before_the_cursor():
    assert drop_hit_points(400, 200, (10, 10)) == [(400, 200), (10, 10)]


def test_zeroed_drop_point_falls_back_to_the_cursor():
    assert drop_hit_points(0, 0, (111, 222)) == [(111, 222)]


def test_zero_drop_and_no_cursor_has_nothing_to_hit_test():
    assert drop_hit_points(0, 0, None) == []


def test_nonzero_x_alone_is_a_real_drop_point():
    assert drop_hit_points(50, 0, (1, 1)) == [(50, 0), (1, 1)]


def test_cursor_matching_the_drop_point_is_not_duplicated():
    assert drop_hit_points(400, 200, (400, 200)) == [(400, 200)]


def test_close_this_window_closes_the_caller_even_when_another_window_is_active(monkeypatch):
    from frontend.ui_web.panel_api_window import PanelApiWindowMixin

    caller, active = object(), object()
    groups = [
        focus_windows._FocusGroup(window=caller, tabs={"file:a.verse": "a"}, wid="focus-caller"),
        focus_windows._FocusGroup(window=active, tabs={"chat:b": "b"}, wid="focus-active"),
    ]
    destroyed: list[object] = []
    monkeypatch.setattr(focus_windows, "_focus_groups", groups)
    monkeypatch.setattr(focus_windows, "_destroy_window", destroyed.append)
    monkeypatch.setattr(focus_windows, "_drop_registry_window", lambda _wid: None)
    monkeypatch.setattr(focus_windows, "_log_close", lambda *_a: None)

    api = PanelApiWindowMixin()
    api._window = object()  # main
    api._resolve_window = lambda: active  # type: ignore[method-assign]
    api.close_this_window("test", "focus-caller")

    assert destroyed == [caller]
    assert [g.wid for g in focus_windows._focus_groups] == ["focus-active"]
