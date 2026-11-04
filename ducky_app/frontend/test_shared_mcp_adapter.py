"""Shared MCP adapter: which daemon an agent joins, slow hellos, and counted fallbacks."""

from __future__ import annotations

import json

import pytest

from backend.bridge import shared_mcp
from backend.bridge.test_shared_mcp import FakeMcp, _serve
from frontend import shared_mcp_adapter as ad


@pytest.fixture
def app_data(monkeypatch, tmp_path):
    """Adapter (LOCALAPPDATA/UEFN-Ducky) and daemon (default_app_data_dir) share one folder."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("UEFN_DUCKY_APP_VERSION", raising=False)
    monkeypatch.delenv(shared_mcp.SLOT_ENV, raising=False)
    folder = tmp_path / "UEFN-Ducky"
    folder.mkdir()
    return folder


@pytest.mark.parametrize("slot", [None, "0", "5", "x"])
def test_the_adapter_reads_the_same_state_file_as_the_daemon(monkeypatch, tmp_path, slot) -> None:
    monkeypatch.setattr("frontend.settings.default_app_data_dir", lambda: tmp_path)
    if slot is None:
        monkeypatch.delenv(shared_mcp.SLOT_ENV, raising=False)
    else:
        monkeypatch.setenv(shared_mcp.SLOT_ENV, slot)
    assert ad._SLOT_ENV == shared_mcp.SLOT_ENV
    assert ad._state_name() == shared_mcp.state_path().name


def test_a_host_agent_joins_its_own_hosts_daemon(monkeypatch, app_data) -> None:
    """Nov 4 2026: every host agent joined the app's daemon; the host's own sat at 0 clients."""
    monkeypatch.setenv(shared_mcp.SLOT_ENV, "0")
    decoy = {"host": "127.0.0.1", "port": 1, "token": "t", "key": {"version": "1", "port": "4200", "project": ""}}
    (app_data / "shared_mcp.json").write_text(json.dumps(decoy), encoding="utf-8")
    monkeypatch.setattr(ad, "_spawn_daemon", lambda args: pytest.fail("the host's daemon was up"))
    thread = _serve(FakeMcp(), monkeypatch, app_data)
    try:
        assert (app_data / "shared_mcp-h0.json").is_file()
        sock = ad.connect(["--port", "4200"], timeout_s=2.0)
        assert sock is not None
        assert sock.getpeername()[1] == shared_mcp.wait_ready(1.0)["port"]
        ad._write_frame(sock, {"op": "bye"})
        sock.close()
    finally:
        shared_mcp.request_stop()
        thread.join(timeout=6)


def test_a_host_agent_never_asks_the_app_for_a_daemon(monkeypatch) -> None:
    """The app has no slot: it would start an unslotted daemon nobody in the host uses."""
    monkeypatch.setenv(shared_mcp.SLOT_ENV, "1")
    monkeypatch.setattr(ad, "_ask_app_to_start", lambda: pytest.fail("asked the app"))
    started: list[list[str]] = []
    monkeypatch.setattr(ad.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    ad._spawn_daemon(["--port", "4200"])
    assert len(started) == 1 and started[0][-1] == "--shared-daemon"


def test_a_slow_hello_is_tried_once_more_before_spawning(monkeypatch, app_data) -> None:
    state = {"host": "127.0.0.1", "port": 9, "token": "t", "key": {}}
    (app_data / "shared_mcp.json").write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(ad, "_HELLO_BACKOFF_S", 0.01)
    tries: list[float] = []
    answered = object()

    def _attempt(_state, read_timeout=ad._HELLO_TIMEOUT_S):
        tries.append(read_timeout)
        return (None, True) if len(tries) == 1 else (answered, False)

    monkeypatch.setattr(ad, "_hello_attempt", _attempt)
    monkeypatch.setattr(ad, "_spawn_daemon", lambda args: pytest.fail("spawned past a slow daemon"))
    assert ad.connect(["--port", "4200"], timeout_s=0.2) is answered
    assert tries == [ad._HELLO_TIMEOUT_S, ad._HELLO_TIMEOUT_S * 2]


def test_a_refused_hello_is_not_retried(monkeypatch, app_data) -> None:
    state = {"host": "127.0.0.1", "port": 9, "token": "t", "key": {}}
    (app_data / "shared_mcp.json").write_text(json.dumps(state), encoding="utf-8")
    tries: list[float] = []
    monkeypatch.setattr(ad, "_hello_attempt", lambda s, read_timeout=10.0: tries.append(read_timeout) or (None, False))
    spawned: list[list[str]] = []
    monkeypatch.setattr(ad, "_spawn_daemon", lambda args: spawned.append(args))
    monkeypatch.setattr(ad, "_try_hello", lambda s: None)
    assert ad.connect(["--port", "4200"], timeout_s=0.2) is None
    assert tries == [10.0] and spawned == [["--port", "4200"]]


def test_each_dedicated_fallback_is_counted(monkeypatch, app_data) -> None:
    monkeypatch.setenv(shared_mcp.SLOT_ENV, "2")
    monkeypatch.setattr(ad, "connect", lambda args: None)
    assert ad.run(["--port", "4200"]) is False
    assert ad.run(["--port", "4200"]) is False
    record = json.loads((app_data / "shared_mcp_fallbacks.json").read_text(encoding="utf-8"))
    assert record["count"] == 2 and record["slot"] == "2" and record["reason"]
