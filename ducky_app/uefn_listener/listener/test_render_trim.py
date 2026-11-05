"""Render trim: a redraw burst after captures/heavy commands that runs until UEFN's
memory is back (or 30 s), forces draws in the background and restores the setting."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

MB = 1024 * 1024


class _FakeSubsystem:
    def __init__(self) -> None:
        self.invalidations = 0
        self.realtime = False
        self.realtime_sets: list[bool] = []

    def editor_invalidate_viewports(self) -> None:
        self.invalidations += 1

    def editor_get_viewport_realtime(self) -> bool:
        return self.realtime

    def editor_set_viewport_realtime(self, on: bool) -> None:
        self.realtime = bool(on)
        self.realtime_sets.append(bool(on))


class _World:
    """Fake clock, memory reader and window state."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.bytes = 30_000 * MB
        self.state = "foreground"
        self.logs: list[str] = []


def _load(fake_sub, world: _World, name="render_trim_under_test"):
    unreal = types.ModuleType("unreal")
    unreal.LevelEditorSubsystem = type("LevelEditorSubsystem", (), {})
    unreal.get_editor_subsystem = lambda _cls: fake_sub
    unreal.EditorLevelLibrary = types.SimpleNamespace(editor_invalidate_viewports=lambda: None)
    sys.modules["unreal"] = unreal
    path = Path(__file__).resolve().parent / "render_trim.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.clock = lambda: world.t
    mod.read_bytes = lambda: world.bytes
    mod.window_state = lambda: world.state
    mod._log = lambda msg, level="info": world.logs.append(msg)
    return mod


@pytest.fixture
def sub():
    return _FakeSubsystem()


@pytest.fixture
def world():
    return _World()


def test_burst_redraws_once_per_tick_then_goes_quiet(sub, world):
    mod = _load(sub, world)
    assert mod.pending() == 0
    mod.tick()
    assert sub.invalidations == 0  # idle ticks cost nothing
    mod.request_trim(3)
    for _ in range(10):
        mod.tick()
    assert sub.invalidations == 3
    assert mod.pending() == 0
    assert sub.realtime_sets == []  # foreground: the realtime setting is never touched


def test_requests_extend_but_never_stack(sub, world):
    mod = _load(sub, world)
    mod.request_trim(5)
    mod.tick()
    mod.request_trim(5)  # a second capture mid-burst
    assert mod.pending() == 5
    mod.request_trim(2)  # a shorter request never cuts a burst short
    assert mod.pending() == 5
    assert mod.TRIM_TICKS == 60


def test_burst_keeps_going_past_60_ticks_until_memory_drops(sub, world):
    mod = _load(sub, world)
    baseline = world.bytes
    world.bytes = baseline + 712 * MB  # the capture grew UEFN
    mod.request_trim(baseline=baseline)
    for _ in range(150):
        world.t += 0.05
        mod.tick()
    assert mod.active()  # 150 ticks, memory still up: not done
    world.bytes = baseline + 18 * MB  # the pool let go
    world.t += 0.05
    mod.tick()
    assert not mod.active()
    assert sub.invalidations == 151
    assert world.logs[-1].startswith("Render trim: +712 MB → +18 MB in ")


def test_burst_stops_at_the_30_second_cap(sub, world):
    mod = _load(sub, world)
    baseline = world.bytes
    world.bytes = baseline + 700 * MB
    mod.request_trim(baseline=baseline)
    ticks = 0
    while mod.active() and ticks < 10_000:
        world.t += 0.5  # a throttled editor: two ticks a second
        mod.tick()
        ticks += 1
    assert not mod.active()
    assert ticks == 60  # 30 s / 0.5 s
    assert "30 s cap" in world.logs[-1]


def test_background_forces_draws_and_restores_realtime(sub, world):
    mod = _load(sub, world)
    world.state = "background"
    baseline = world.bytes
    world.bytes = baseline + 705 * MB
    mod.request_trim(4, baseline=baseline)
    mod.tick()
    assert sub.realtime is True  # override on for the burst
    world.bytes = baseline + 22 * MB
    for _ in range(5):
        mod.tick()
    assert not mod.active()
    assert sub.realtime is False  # the person's setting is back
    assert sub.realtime_sets == [True, False]
    assert "(background, realtime override)" in world.logs[-1]


def test_realtime_already_on_is_left_alone(sub, world):
    mod = _load(sub, world)
    sub.realtime = True
    world.state = "background"
    mod.request_trim(2)
    for _ in range(3):
        mod.tick()
    assert sub.realtime_sets == []
    assert sub.realtime is True


def test_minimized_editor_parks_the_trim_and_runs_on_restore(sub, world):
    mod = _load(sub, world)
    baseline = world.bytes
    world.bytes = baseline + 700 * MB
    world.state = "minimized"
    mod.request_trim(3, baseline=baseline)
    for _ in range(100):
        world.t += 1.0  # 100 s minimized: past the 30 s cap
        mod.tick()
    assert sub.invalidations == 0
    assert mod.active()
    assert world.logs.count("Render trim waiting: UEFN minimized") == 1
    world.state = "foreground"
    mod.tick()
    assert mod.active()  # restored: the cap restarted, memory still up
    world.bytes = baseline
    mod.tick()
    mod.tick()
    assert not mod.active()
    assert sub.invalidations == 3


def test_fallback_to_level_library_when_no_subsystem(world):
    calls = []
    unreal = types.ModuleType("unreal")
    unreal.LevelEditorSubsystem = type("LevelEditorSubsystem", (), {})
    unreal.get_editor_subsystem = lambda _cls: None
    unreal.EditorLevelLibrary = types.SimpleNamespace(editor_invalidate_viewports=lambda: calls.append(1))
    sys.modules["unreal"] = unreal
    path = Path(__file__).resolve().parent / "render_trim.py"
    spec = importlib.util.spec_from_file_location("render_trim_under_test_fb", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.clock = lambda: world.t
    mod.read_bytes = lambda: world.bytes
    mod.window_state = lambda: "foreground"
    mod.request_trim(2)
    mod.tick()
    mod.tick()
    assert calls == [1, 1]


def test_a_failing_redraw_ends_the_burst_quietly(sub, world):
    mod = _load(sub, world)

    def boom():
        raise RuntimeError("no viewport")

    sub.editor_invalidate_viewports = boom
    mod.request_trim(10)
    mod.tick()
    assert mod.pending() == 0
    mod.tick()  # stays quiet afterwards
