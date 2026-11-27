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


def test_nudge_starts_one_animation_on_windows_and_macos(monkeypatch) -> None:
    started: list[object] = []

    class FakeThread:
        def __init__(self, **_kw) -> None:
            pass

        def start(self) -> None:
            started.append(1)

    monkeypatch.setattr(tc.threading, "Thread", FakeThread)
    monkeypatch.setattr(tc, "_running", False)
    monkeypatch.setattr(tc, "_failed", False)
    monkeypatch.setattr(tc.platform_info, "IS_LINUX", False)
    monkeypatch.setattr(tc.sys, "platform", "freebsd")
    tc.nudge()
    assert started == []
    monkeypatch.setattr(tc.sys, "platform", "win32")
    tc.nudge()
    tc.nudge()
    assert started == [1]
    monkeypatch.setattr(tc, "_running", False)
    monkeypatch.setattr(tc.sys, "platform", "darwin")
    tc.nudge()
    tc.nudge()
    assert started == [1, 1]


def _fake_cocoa(monkeypatch, later: list):
    import sys
    from types import ModuleType, SimpleNamespace

    class Obj:
        """Any Cocoa object: alloc/init chains return itself and every setter is recorded."""

        def __init__(self) -> None:
            self.calls: list = []

        @classmethod
        def alloc(cls):
            return cls()

        def __getattr__(self, name):
            def call(*args):
                self.calls.append((name, args))
                return self
            return call

    screen = SimpleNamespace(frame=lambda: SimpleNamespace(size=SimpleNamespace(height=900.0)),
                             backingScaleFactor=lambda: 2.0)
    appkit = ModuleType("AppKit")
    appkit.NSWindow, appkit.NSImageView, appkit.NSImage = type("W", (Obj,), {}), type("V", (Obj,), {}), type("I", (Obj,), {})
    appkit.NSColor = SimpleNamespace(clearColor=lambda: "clear")
    appkit.NSData = SimpleNamespace(dataWithBytes_length_=lambda b, n: b)
    appkit.NSScreen = SimpleNamespace(screens=lambda: [screen], mainScreen=lambda: screen)
    appkit.NSScreenSaverWindowLevel = 1000
    appkit.NSEvent = SimpleNamespace(mouseLocation=lambda: SimpleNamespace(x=100.0, y=600.0))
    helper = ModuleType("PyObjCTools")
    helper.AppHelper = SimpleNamespace(callAfter=lambda fn: later.append(("now", fn)),
                                       callLater=lambda s, fn: later.append((s, fn)))
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setitem(sys.modules, "PyObjCTools", helper)


def test_macos_pointer_is_a_click_through_window_at_the_cursor_tip(monkeypatch) -> None:
    later: list = []
    _fake_cocoa(monkeypatch, later)
    monkeypatch.setattr(tc.sys, "platform", "darwin")
    monkeypatch.setattr(tc, "_pointer", None)
    monkeypatch.setattr(tc, "_failed", False)
    monkeypatch.setattr(tc, "_running", True)
    monkeypatch.setattr(tc, "_last_move", tc.time.monotonic())
    monkeypatch.setattr(tc, "HOLD_S", 60.0)  # a slow Intel runner let 0.18 s pass before the first tick
    monkeypatch.setattr(tc, "load_duck", lambda: None)
    assert tc._cursor_pos() == (200, 600)  # points (100, 900 - 600) at 2x, top-left
    tc._start()
    when, begin = later.pop()
    assert when == "now"
    begin()
    pointer = tc._pointer
    assert pointer is not None and not tc._failed
    ns = pointer.window.ns
    names = [n for n, _ in ns.calls]
    assert ("setIgnoresMouseEvents_", (True,)) in ns.calls and ("setOpaque_", (False,)) in ns.calls
    assert "orderFrontRegardless" in names
    (x, y), (w, h) = next(a[0] for n, a in reversed(ns.calls) if n == "setFrame_display_")
    margin_pt = pointer.margin / 2.0
    assert (x, round(900 - y - h, 3)) == (100 - margin_pt, round(300 - margin_pt, 3))  # tip on the cursor
    assert later and later[-1][0] == tc.FRAME_MS / 1000.0  # the next tick is on the Cocoa loop
    pointer.window.destroy()
    assert ns.calls[-1][0] == "close"


def test_nudge_on_linux_x11_draws_and_wayland_says_no(monkeypatch) -> None:
    started: list[object] = []

    class FakeThread:
        def __init__(self, **_kw) -> None:
            pass

        def start(self) -> None:
            started.append(1)

    monkeypatch.setattr(tc.threading, "Thread", FakeThread)
    monkeypatch.setattr(tc.platform_info, "IS_LINUX", True)
    monkeypatch.setattr(tc, "_running", False)
    monkeypatch.setattr(tc, "_failed", False)
    monkeypatch.setattr(tc.platform_info, "session_type", lambda: "wayland")
    tc.nudge()
    assert started == [] and tc._failed is True  # off for the session, logged once
    monkeypatch.setattr(tc, "_failed", False)
    monkeypatch.setattr(tc.platform_info, "session_type", lambda: "x11")
    tc.nudge()
    assert started == [1]


def test_a_failure_turns_it_off_quietly(monkeypatch) -> None:
    monkeypatch.setattr(tc, "_running", True)
    monkeypatch.setattr(tc, "_failed", False)
    tc._give_up("test")
    assert tc._failed is True and tc._running is False
    monkeypatch.setattr(tc, "_failed", False)
