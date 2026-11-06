"""heap_trim: throttle, threshold, background thread, no-op off Windows, and a real trim that
gives a freed 300 MB back to Windows."""

from __future__ import annotations

import gc
import sys
import threading

import pytest

from frontend import heap_trim


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(heap_trim, "_last", 0.0)
    monkeypatch.setattr(heap_trim, "_running", False)


def _fake(monkeypatch, free_mb: float) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(heap_trim.sys, "platform", "win32")
    monkeypatch.setattr(heap_trim, "heap_free_mb", lambda: free_mb)
    monkeypatch.setattr(heap_trim, "_compact", lambda: calls.append("compact"))
    monkeypatch.setattr(heap_trim, "_private_bytes", lambda: 1000 * heap_trim.MB - len(calls) * 600 * heap_trim.MB)
    return calls


def _join() -> None:
    for t in threading.enumerate():
        if t.name == "heap-trim":
            t.join(5)


def test_trim_runs_on_a_background_thread_and_reports_mb_freed(monkeypatch) -> None:
    calls = _fake(monkeypatch, 700)
    seen: list[str] = []
    monkeypatch.setattr(heap_trim, "_compact", lambda: (calls.append("compact"), seen.append(threading.current_thread().name)))
    assert heap_trim.maybe_trim(now=1000.0) is True
    _join()
    assert seen == ["heap-trim"]
    assert heap_trim._running is False


def test_trim_returns_freed_mb_and_time(monkeypatch) -> None:
    _fake(monkeypatch, 700)
    got = heap_trim.trim()
    assert got["freed_mb"] == 600.0 and got["took_ms"] >= 0


def test_at_most_every_five_minutes(monkeypatch) -> None:
    calls = _fake(monkeypatch, 700)
    assert heap_trim.maybe_trim(now=1000.0)
    _join()
    assert not heap_trim.maybe_trim(now=1000.0 + heap_trim.EVERY_S - 1)
    assert heap_trim.maybe_trim(now=1000.0 + heap_trim.EVERY_S + 1)
    _join()
    assert calls == ["compact", "compact"]


def test_skips_when_little_heap_is_free(monkeypatch) -> None:
    calls = _fake(monkeypatch, heap_trim.FREE_OVER_MB - 1)
    assert not heap_trim.maybe_trim(now=1000.0)
    _join()
    assert calls == [] and heap_trim._last == 0.0  # not throttled: the next tick checks again


def test_one_trim_at_a_time(monkeypatch) -> None:
    _fake(monkeypatch, 700)
    monkeypatch.setattr(heap_trim, "_running", True)
    assert not heap_trim.maybe_trim(now=1000.0)


def test_a_failing_compact_never_raises(monkeypatch) -> None:
    _fake(monkeypatch, 700)

    def boom() -> None:
        raise OSError("no heap")

    monkeypatch.setattr(heap_trim, "_compact", boom)
    got = heap_trim.trim()
    assert got["freed_mb"] == 0.0 and "no heap" in got["error"]


def test_noop_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(heap_trim.sys, "platform", "linux")
    monkeypatch.setattr(heap_trim, "_compact", lambda: pytest.fail("compacted off Windows"))
    assert heap_trim.trim() == {"freed_mb": 0.0, "took_ms": 0.0, "skipped": "not win32"}
    assert heap_trim.maybe_trim(now=1000.0) is False
    assert heap_trim.heap_free_mb() == 0.0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows heap")
def test_a_freed_300_mb_goes_back_to_windows() -> None:
    # 8 KB blocks: past pymalloc, inside the heap's low-fragmentation buckets.
    blocks = [bytearray(8 * 1024) for _ in range(300 * 128)]
    peak = heap_trim._private_bytes()
    blocks.clear()
    got = heap_trim.trim()
    dropped_mb = (peak - heap_trim._private_bytes()) / heap_trim.MB
    assert dropped_mb >= 150, (got, dropped_mb)
    assert got["took_ms"] < 2000


@pytest.mark.skipif(sys.platform != "win32", reason="Windows heap")
def test_trim_on_a_fragmented_heap_never_grows_it() -> None:
    # One survivor in 50 pins the buckets: trim can only give back whole free pages.
    blocks = [bytearray(8 * 1024) for _ in range(100 * 128)]
    keep = blocks[::50]
    blocks.clear()
    gc.collect()
    before = heap_trim._private_bytes()
    got = heap_trim.trim()
    assert got["freed_mb"] >= 0 and "error" not in got
    assert heap_trim._private_bytes() <= before + 8 * heap_trim.MB
    keep.clear()
