"""focus_windows.drop_hit_points — drop coords first, live cursor as fallback."""

from __future__ import annotations

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
