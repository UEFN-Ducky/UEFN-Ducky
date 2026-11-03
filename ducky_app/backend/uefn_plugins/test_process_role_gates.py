"""App-only work never starts in an agent host or a tool server."""

from __future__ import annotations

import threading

import pytest

from backend import process_role
from backend.uefn_plugins import host, scopes, sync_scheduler


@pytest.fixture(params=["agent_host", "tool_server"])
def not_app(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.delenv("UEFN_DUCKY_MCP_BRIDGE", raising=False)
    monkeypatch.setenv(process_role.ENV, request.param)
    return request.param


def test_crash_heal_starts_no_thread(not_app: str, monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[str] = []
    monkeypatch.setattr(host, "_read_crashed", lambda: {"x": {}})
    monkeypatch.setattr(threading.Thread, "start", lambda self: started.append(self.name))
    host._start_crash_heal()
    assert started == []


def test_crash_heal_runs_in_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UEFN_DUCKY_MCP_BRIDGE", raising=False)
    monkeypatch.delenv(process_role.ENV, raising=False)
    started: list[str] = []
    monkeypatch.setattr(host, "_read_crashed", lambda: {"x": {}})
    monkeypatch.setattr(threading.Thread, "start", lambda self: started.append(self.name))
    host._start_crash_heal()
    assert started == ["uefn-plugin-crash-heal"]


def test_sync_scheduler_does_not_start(not_app: str) -> None:
    assert sync_scheduler.start() is False


def test_a_plugin_write_is_an_agents(not_app: str) -> None:
    assert scopes.caller() == ("agent", "")
