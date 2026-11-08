from __future__ import annotations

from PIL import Image

from frontend.ui_web import trackpad_cursor as tc


def test_size_eases_in_while_moving_and_all_the_way_out_after() -> None:
    level = 0.0
    for _ in range(40):
        level = tc.next_level(level, True)
    assert level == 1.0
    first_drop = tc.next_level(level, False)
    assert 0.0 < first_drop < 1.0
    for _ in range(60):
        level = tc.next_level(level, False)
    assert level == 0.0


def test_levels_map_onto_every_frame() -> None:
    assert tc.frame_index(0.01) == 0
    assert tc.frame_index(1.0) == tc.FRAMES - 1


def test_the_tip_stays_put_as_it_grows_and_the_last_frame_is_solid() -> None:
    full, margin = tc.render_pointer(96, None)
    frames = tc.render_frames(full, margin)
    assert len(frames) == tc.FRAMES and {f.size for f in frames} == {full.size}
    inside = (margin + 4, margin + 20)
    assert frames[-1].getpixel(inside)[3] == 255
    assert frames[0].getpixel(inside)[3] < 60
    assert frames[0].getchannel("A").getbbox()[2] < frames[-1].getchannel("A").getbbox()[2]


def test_yellow_inside_a_black_outline() -> None:
    full, margin = tc.render_pointer(132, None)
    red, green, blue, _alpha = full.getpixel((margin + 8, margin + 50))
    assert red > 230 and green > 170 and blue < 120
    edge = full.getpixel((margin - 7, margin + 50))
    assert edge[3] == 255 and max(edge[:3]) < 40


def test_the_real_duck_is_drawn_on_the_arrow() -> None:
    plain, margin = tc.render_pointer(132, None)
    duck = Image.new("RGBA", (64, 64), (200, 0, 200, 255))
    marked, _ = tc.render_pointer(132, duck)
    left, top, right, bottom = (int(v * 132) for v in tc._DUCK_BOX)
    spot = (margin + (left + right) // 2, margin + (top + bottom) // 2)
    assert marked.getpixel(spot)[:3] != plain.getpixel(spot)[:3]
    assert tc.load_duck() is not None


def test_nudge_starts_one_animation_and_only_on_windows(monkeypatch) -> None:
    started: list[object] = []

    class FakeThread:
        def __init__(self, **_kw) -> None:
            pass

        def start(self) -> None:
            started.append(1)

    monkeypatch.setattr(tc.threading, "Thread", FakeThread)
    monkeypatch.setattr(tc, "_running", False)
    monkeypatch.setattr(tc, "_failed", False)
    monkeypatch.setattr(tc.sys, "platform", "linux")
    tc.nudge()
    assert started == []
    monkeypatch.setattr(tc.sys, "platform", "win32")
    tc.nudge()
    tc.nudge()
    assert started == [1]


def test_a_failure_turns_it_off_quietly(monkeypatch) -> None:
    monkeypatch.setattr(tc, "_running", True)
    monkeypatch.setattr(tc, "_failed", False)
    tc._give_up("test")
    assert tc._failed is True and tc._running is False
    monkeypatch.setattr(tc, "_failed", False)
