"""Presence beats keep the PC online inside the site's window (remote_link.presence)."""

from __future__ import annotations

import random
import threading
import time

from frontend.ui_web.remote_link import presence as pr

SITE_LIVE_S = 330.0


def test_interval_is_under_a_third_of_the_window() -> None:
    assert pr.beat_interval(SITE_LIVE_S) < SITE_LIVE_S / 3
    assert pr.beat_interval(None) < pr.DEFAULT_LIVE_S / 3
    assert pr.beat_interval(6) == pr.MIN_INTERVAL_S


def test_not_available_only_after_three_missed_beats() -> None:
    iv = pr.beat_interval(SITE_LIVE_S)
    assert pr.is_online(1000.0, 1000.0 + iv * 2.9, iv)
    assert not pr.is_online(1000.0, 1000.0 + iv * 3.1, iv)
    assert not pr.is_online(0.0, 5.0, iv)


def test_ten_minutes_with_failures_never_flaps_offline() -> None:
    """Fake clock, 1 in 4 beats fails (busy network): the site never sees a gap past its window."""
    clock = {"t": 0.0}
    rng = random.Random(7)
    seen: list[float] = []

    def send() -> bool:
        clock["t"] += 0.5  # POST latency
        if rng.random() < 0.25:
            return False
        seen.append(clock["t"])
        return True

    beat = pr.PresenceBeat(send, lambda: SITE_LIVE_S, clock=lambda: clock["t"])
    while clock["t"] < 600.0:
        clock["t"] += beat.beat_once()
    gaps = [b - a for a, b in zip(seen, seen[1:])]
    assert gaps and max(gaps) < SITE_LIVE_S / 2
    flaps = sum(1 for g in gaps if g > SITE_LIVE_S)
    assert flaps == 0


def test_a_failed_beat_retries_soon_not_a_whole_interval() -> None:
    beat = pr.PresenceBeat(lambda: False, lambda: SITE_LIVE_S, clock=lambda: 0.0)
    assert beat.beat_once() == pr.RETRY_S[0]
    assert beat.beat_once() == pr.RETRY_S[1]
    beat._send = lambda: True
    assert beat.beat_once() == pr.beat_interval(SITE_LIVE_S) and beat.failures == 0


def test_beats_run_on_their_own_thread_under_300_busy_agents(monkeypatch) -> None:
    """300 calls of agent work hold every shared pool; the beat still goes out at once."""
    from frontend.ui_web import call_pools

    monkeypatch.setattr(call_pools, "_pools", {})
    stop = threading.Event()
    for _ in range(300):
        call_pools.submit("list_project_file_paths", stop.wait, 5.0)
    beats: list[str] = []
    got = threading.Event()

    def send() -> bool:
        beats.append(threading.current_thread().name)
        got.set()
        return True

    beat = pr.PresenceBeat(send, lambda: SITE_LIVE_S)
    t0 = time.monotonic()
    beat.start()
    try:
        assert got.wait(2.0)
        assert time.monotonic() - t0 < 0.5
        assert beats[0] == "remote-presence"
    finally:
        beat.stop()
        stop.set()
