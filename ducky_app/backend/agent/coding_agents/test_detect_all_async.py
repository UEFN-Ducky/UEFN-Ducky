"""detect_all must not run CLI probes on the caller thread."""

from __future__ import annotations

import threading
import time

import backend.agent.coding_agents.base as base


def _reset_detect() -> None:
    with base._detect_lock:
        base._detect_cache = None
        base._detect_cache_at = 0.0
    with base._detect_refresh_lock:
        base._detect_refresh_inflight = False


def test_detect_all_returns_before_cli_probe(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def _slow_list(_settings=None):
        started.set()
        release.wait(timeout=2)
        return []

    monkeypatch.setattr(base, "list_coding_agents", _slow_list)
    monkeypatch.setattr(base, "_instant_detect_payload", lambda: {"agents": [], "checking": True})
    _reset_detect()
    t0 = time.perf_counter()
    payload = base.detect_all()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert elapsed_ms < 200.0, f"detect_all blocked: {elapsed_ms:.0f}ms"
    assert payload.get("checking") is True
    assert started.wait(timeout=1.0)
    release.set()


def test_detect_all_returns_stale_cache_while_refreshing(monkeypatch):
    stale = {"agents": [{"id": "ducky", "available": True}]}
    started = threading.Event()
    release = threading.Event()

    def _slow_list(_settings=None):
        started.set()
        release.wait(timeout=2)
        return []

    monkeypatch.setattr(base, "list_coding_agents", _slow_list)
    _reset_detect()
    with base._detect_lock:
        base._detect_cache = stale
        base._detect_cache_at = 0.0
    payload = base.detect_all()
    assert payload is stale
    assert started.wait(timeout=1.0)
    release.set()
