"""Per-command UEFN growth ledger: rows, spike vs kept, bounded rows, top 10."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MB = 1024 * 1024


class _World:
    def __init__(self) -> None:
        self.t = 1000.0
        self.bytes = 30_000 * MB
        self.pid = 4242


@pytest.fixture
def env():
    path = Path(__file__).resolve().parent / "memory_ledger.py"
    spec = importlib.util.spec_from_file_location("memory_ledger_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    world = _World()
    mod.clock = lambda: world.t
    mod.read_bytes = lambda: world.bytes
    mod.current_pid = lambda: world.pid
    return mod, world, {}


def _run(mod, world, store, command, grow_mb):
    before = world.bytes
    world.bytes += int(grow_mb * MB)
    mod.record(store, command, before, world.bytes)


def test_rows_sum_correctly(env):
    mod, world, store = env
    _run(mod, world, store, "take_high_res_screenshot", 700)
    _run(mod, world, store, "take_high_res_screenshot", 300)
    _run(mod, world, store, "spawn_actor", 2)
    _run(mod, world, store, "spawn_actor", -5)  # shrinking never counts as growth
    world.t += 61
    mod.settle(store)
    rows = {r["command"]: r for r in mod.snapshot(store)["rows"]}
    assert rows["take_high_res_screenshot"]["calls"] == 2
    assert rows["take_high_res_screenshot"]["grew_mb"] == pytest.approx(1000)
    assert rows["take_high_res_screenshot"]["kept_mb"] == pytest.approx(1000 - 3, abs=1)
    assert rows["spawn_actor"]["calls"] == 2
    assert rows["spawn_actor"]["grew_mb"] == pytest.approx(2)


def test_a_spike_released_within_60_s_is_not_kept(env):
    mod, world, store = env
    base = world.bytes
    _run(mod, world, store, "render_material_preview", 500)
    world.t += 10
    mod.settle(store)  # not due yet: nothing credited
    world.bytes = base  # given back
    world.t += 55
    mod.settle(store)
    row = mod.snapshot(store)["rows"][0]
    assert row["grew_mb"] == pytest.approx(500)
    assert row["kept_mb"] == pytest.approx(0)


def test_kept_is_capped_at_the_original_growth(env):
    mod, world, store = env
    _run(mod, world, store, "a", 100)
    world.bytes += 5000 * MB  # later growth by someone else
    world.t += 61
    mod.settle(store)
    assert mod.snapshot(store)["rows"][0]["kept_mb"] == pytest.approx(100)


def test_one_row_per_name_after_1000_calls(env):
    mod, world, store = env
    names = ["ping", "status", "spawn_actor", "take_high_res_screenshot"]
    for i in range(1000):
        _run(mod, world, store, names[i % len(names)], 1)
        world.t += 0.1
        mod.settle(store)
    led = store["memory_ledger"]
    assert sorted(led["rows"]) == sorted(names)
    assert sum(r["calls"] for r in led["rows"].values()) == 1000
    assert len(led["pending"]) <= mod.MAX_PENDING


def test_snapshot_returns_top_10_by_kept(env):
    mod, world, store = env
    for i in range(15):
        _run(mod, world, store, f"cmd{i:02d}", i + 1)
    world.t += 61
    mod.settle(store)
    snap = mod.snapshot(store)
    assert snap["uefn_mb_now"] == round(world.bytes / MB)
    assert len(snap["rows"]) == 10
    assert snap["rows"][0]["command"] == "cmd14"
    kept = [r["kept_mb"] for r in snap["rows"]]
    assert kept == sorted(kept, reverse=True)


def test_reset_when_uefn_pid_changes(env):
    mod, world, store = env
    _run(mod, world, store, "a", 10)
    world.pid = 9999  # UEFN restarted; the metrics dict came back from elsewhere
    assert mod.snapshot(store)["rows"] == []


def test_unreadable_memory_counts_calls_only(env):
    mod, world, store = env
    mod.record(store, "a", 0, 0)
    row = mod.snapshot(store)["rows"][0]
    assert row["calls"] == 1 and row["grew_mb"] == 0
