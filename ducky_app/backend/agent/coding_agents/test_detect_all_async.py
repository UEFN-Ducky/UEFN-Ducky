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


def _slow_adapter(monkeypatch, started: threading.Event, release: threading.Event, wait_s: float = 2.0):
    """One external agent whose detect() blocks until released."""

    class _Slow:
        def detect(self, _settings):
            started.set()
            release.wait(timeout=wait_s)
            return base.CodingAgentInfo(id="slow", label="Slow", enabled=True, available=True, status="ok")

    monkeypatch.setattr(base, "listed_external_coding_agents", lambda: ["slow"])
    monkeypatch.setattr(base, "get_adapter", lambda _aid: _Slow())


def test_detect_all_returns_before_cli_probe(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    _slow_adapter(monkeypatch, started, release)
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
    _slow_adapter(monkeypatch, started, release)
    monkeypatch.setattr(base, "_instant_detect_payload", lambda: {"agents": [], "checking": True})
    _reset_detect()
    with base._detect_lock:
        base._detect_cache = stale
        base._detect_cache_at = 0.0
    payload = base.detect_all()
    assert payload is stale
    assert started.wait(timeout=1.0)
    release.set()


def test_fast_agent_publishes_before_slow_agent(monkeypatch):
    """One slow plugin (Codex fetching pricing) must not hold the others on Checking…"""
    release = threading.Event()

    class _Fast:
        def detect(self, _settings):
            return base.CodingAgentInfo(id="fast", label="Fast", enabled=True, available=True, status="ok")

    class _Slow:
        def detect(self, _settings):
            release.wait(timeout=5)
            return base.CodingAgentInfo(id="slow", label="Slow", enabled=True, available=True, status="ok")

    monkeypatch.setattr(base, "listed_external_coding_agents", lambda: ["fast", "slow"])
    monkeypatch.setattr(base, "get_adapter", lambda aid: _Fast() if aid == "fast" else _Slow())
    monkeypatch.setattr(
        base,
        "_instant_detect_payload",
        lambda: {"agents": [{"id": a, "status": "Checking…"} for a in ("fast", "slow")], "checking": True},
    )
    _reset_detect()
    base.detect_all()
    deadline = time.time() + 2.0
    fast_row = None
    while time.time() < deadline and fast_row is None:
        rows = {a["id"]: a for a in base.detect_all().get("agents", [])}
        if rows.get("fast", {}).get("status") == "ok":
            fast_row = rows["fast"]
            assert rows["slow"]["status"] == "Checking…"
        time.sleep(0.02)
    release.set()
    assert fast_row is not None, "fast agent never published while slow one was probing"
