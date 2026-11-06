"""detect_all must not run CLI probes on the caller thread."""

from __future__ import annotations

import threading
import time

import pytest

import backend.agent.coding_agents.base as base


@pytest.fixture(autouse=True)
def _saved_detect_in_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(base, "_detect_saved_path", lambda: tmp_path / "coding_agents_detect.json")
    monkeypatch.setattr(base, "_detect_saved_read", True)  # tests opt in to the boot read


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
    # The probe blocks for 2 s until released, so returning well before that
    # proves detect_all did not wait for it; a tighter bound only measured how
    # fast a busy PC starts the worker thread (it failed a release at 100% CPU).
    assert elapsed_ms < 1500.0, f"detect_all blocked on the CLI probe: {elapsed_ms:.0f}ms"
    assert payload.get("checking") is True
    assert started.wait(timeout=5.0)
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


def test_opening_chats_and_settings_does_not_reprobe_every_cli(monkeypatch):
    """Each probe spawns the CLI (`claude --version`, `auth status`); a version or login
    changes about once a day, and every real change already expires the cache."""
    from types import SimpleNamespace

    calls = {"a": 0, "b": 0}

    class _Counting:
        def __init__(self, aid):
            self.aid = aid

        def detect(self, _settings):
            calls[self.aid] += 1
            return base.CodingAgentInfo(id=self.aid, label=self.aid, enabled=True, available=True, status="ok")

    monkeypatch.setattr(base, "listed_external_coding_agents", lambda: ["a", "b"])
    monkeypatch.setattr(base, "get_adapter", lambda aid: _Counting(aid))
    monkeypatch.setattr(
        base, "_instant_detect_payload", lambda: {"agents": [{"id": a} for a in ("a", "b")], "checking": True}
    )
    clock = [1000.0]
    monkeypatch.setattr(base, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    _reset_detect()

    def list_agents():
        base.detect_all()
        deadline = time.monotonic() + 5
        while base._detect_refresh_inflight and time.monotonic() < deadline:
            time.sleep(0.01)

    for _ in range(20):  # chat switches, Settings and the Setup wizard over a minute
        list_agents()
        clock[0] += 3.0
    assert calls == {"a": 1, "b": 1}
    base.invalidate_detect_cache()  # a key, setting or plugin change
    list_agents()
    list_agents()
    assert calls == {"a": 2, "b": 2}


def test_a_start_serves_the_saved_result_and_probes_a_minute_later(monkeypatch) -> None:
    from types import SimpleNamespace

    calls = []

    class _Counting:
        def detect(self, _settings):
            calls.append(1)
            return base.CodingAgentInfo(id="a", label="a", enabled=True, available=True, status="ok")

    monkeypatch.setattr(base, "listed_external_coding_agents", lambda: ["a"])
    monkeypatch.setattr(base, "get_adapter", lambda aid: _Counting())
    monkeypatch.setattr(base, "_instant_detect_payload", lambda: {"agents": [{"id": "a"}], "checking": True})
    clock = [1000.0]
    monkeypatch.setattr(base, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def wait_refresh():
        deadline = time.monotonic() + 5
        while base._detect_refresh_inflight and time.monotonic() < deadline:
            time.sleep(0.01)

    _reset_detect()
    base.detect_all()  # no saved result: probes and saves it
    wait_refresh()
    assert calls == [1]
    assert base._read_saved_detect()["agents"][0]["status"] == "ok"

    _reset_detect()  # the next Ducky start
    monkeypatch.setattr(base, "_detect_saved_read", False)
    payload = base.detect_all()
    wait_refresh()
    assert payload["agents"][0]["status"] == "ok" and "checking" not in payload
    assert calls == [1]  # served the saved result: no CLI started
    clock[0] += base._DETECT_BOOT_RECHECK_SEC + 1
    base.detect_all()
    wait_refresh()
    assert calls == [1, 1]  # probed again a minute after the start


def test_a_checking_agent_already_names_its_gateway(monkeypatch) -> None:
    """While its CLI probe runs, an agent shows under its gateway (setup, Settings → LLMs)."""
    from backend.uefn_plugins.plugin_host.testing import patch_host

    monkeypatch.setattr(base, "listed_external_coding_agents", lambda: ["claude_code"])
    patch_host(
        monkeypatch,
        "get_contributions",
        lambda: {"llm_coding_agents": [{"id": "claude_code", "label": "Claude Code", "plugin_id": "Anthropic"}]},
    )
    rows = {a["id"]: a for a in base._instant_detect_payload()["agents"]}
    assert rows["claude_code"]["status"] == "Checking…"
    assert rows["claude_code"]["plugin_id"] == "anthropic"
    assert rows["claude_code"]["label"] == "Claude Code"
