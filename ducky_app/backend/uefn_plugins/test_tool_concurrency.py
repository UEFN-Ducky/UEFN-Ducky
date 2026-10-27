"""plugin.json "tool_concurrency": how many of one plugin's tool calls run at once."""

from __future__ import annotations

import threading
import time

import pytest

from backend.uefn_plugins import host


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(host, "_TOOL_CONCURRENCY", {})
    monkeypatch.setattr(host, "_TOOL_SLOTS", {})


@pytest.mark.parametrize("raw,want", [(1, 1), (3, 3), (500, 64), (0, 8), (-2, 8), ("2", 8), (True, 8), (None, 8)])
def test_manifest_value(raw, want):
    assert host.manifest_tool_concurrency({"tool_concurrency": raw} if raw is not None else {}) == want


def _overlap(pid: str, calls: int) -> int:
    """Run ``calls`` slow calls on threads; return the most that ran at once."""
    running = peak = 0
    lock = threading.Lock()

    def call():
        nonlocal running, peak
        with host.plugin_tool_slot(pid):
            with lock:
                running += 1
                peak = max(peak, running)
            time.sleep(0.05)
            with lock:
                running -= 1

    threads = [threading.Thread(target=call) for _ in range(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return peak


def test_one_at_a_time_when_declared():
    host._TOOL_CONCURRENCY["blender"] = 1
    assert _overlap("blender", 4) == 1


def test_default_lets_eight_run():
    assert _overlap("imk", 10) == 8


def test_a_call_inside_the_same_plugin_reuses_its_slot():
    host._TOOL_CONCURRENCY["solo"] = 1
    with host.plugin_tool_slot("solo"):
        with host.plugin_tool_slot("solo"):  # would deadlock without reuse
            pass


def test_busy_after_the_wait(monkeypatch):
    monkeypatch.setattr(host, "TOOL_SLOT_WAIT_S", 0.05)
    host._TOOL_CONCURRENCY["solo"] = 1
    started, release = threading.Event(), threading.Event()

    def hold():
        with host.plugin_tool_slot("solo"):
            started.set()
            release.wait(2)

    t = threading.Thread(target=hold)
    t.start()
    started.wait(2)
    try:
        with pytest.raises(ValueError, match=r"Plugin 'solo' is busy \(1 of its calls are running\)"):
            with host.plugin_tool_slot("solo"):
                pass
    finally:
        release.set()
        t.join()
