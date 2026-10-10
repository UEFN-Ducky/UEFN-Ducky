"""Cold-server timing and launch admission without starting real agents."""
from types import SimpleNamespace
import threading

import pytest

from backend.agent.coding_agents import readiness
from backend.bridge import shared_mcp


@pytest.mark.parametrize("agent", ["codex", "claude_code", "cursor"])
@pytest.mark.parametrize("session", ["", "existing-thread"])
def test_cold_server_answers_after_eight_seconds_before_first_tool(monkeypatch, agent, session):
    now = [0.0]
    events = []
    monkeypatch.setattr(readiness.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(readiness.time, "sleep", lambda seconds: now.__setitem__(0, now[0]+seconds))
    monkeypatch.setattr(shared_mcp, "start_daemon_from_app", lambda: events.append("start") or {"ok": True})
    monkeypatch.setattr(shared_mcp, "_daemon_answers", lambda **kw: now[0] >= 8)
    def launch(**kwargs):
        assert now[0] == 8
        assert kwargs["session_id"] == session
        events.append((agent, "ducky_get_plan"))
        return "answer with tools"
    assert readiness.launch_with_ready_tools(SimpleNamespace(launch=launch), session_id=session) == "answer with tools"
    assert events == ["start", (agent, "ducky_get_plan")]


def test_dead_server_times_out_without_launch(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(readiness.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(readiness.time, "sleep", lambda seconds: now.__setitem__(0, now[0]+seconds))
    monkeypatch.setattr(shared_mcp, "start_daemon_from_app", lambda: {"ok": True})
    def probe(timeout_s):
        now[0] += timeout_s
        return False
    monkeypatch.setattr(shared_mcp, "_daemon_answers", probe)
    adapter = SimpleNamespace(launch=lambda **kw: pytest.fail("Agent launched before tools"))
    result = readiness.launch_with_ready_tools(adapter, session_id="existing")
    assert now[0] == 20
    assert not result.ok and result.error == "Ducky's tools didn't start"
    assert result.upstream_session_id == "existing"


@pytest.mark.parametrize("outcome", ["disabled", "exception", "cancelled"])
def test_no_launch_on_start_failure_or_cancellation(monkeypatch, outcome):
    cancel = threading.Event()
    if outcome == "cancelled": cancel.set()
    def start():
        if outcome == "exception": raise OSError("private details")
        assert outcome != "cancelled"
        return {"ok": False}
    monkeypatch.setattr(shared_mcp, "start_daemon_from_app", start)
    result = readiness.launch_with_ready_tools(SimpleNamespace(launch=lambda **kw: pytest.fail("launched")), cancel=cancel)
    assert not result.ok
    assert result.status == ("cancelled" if outcome == "cancelled" else "error")


def test_cancel_during_wait_prevents_launch(monkeypatch):
    cancel = threading.Event()
    monkeypatch.setattr(shared_mcp, "start_daemon_from_app", lambda: {"ok": True})
    def probe(**kwargs):
        cancel.set()
        return True
    monkeypatch.setattr(shared_mcp, "_daemon_answers", probe)
    result = readiness.launch_with_ready_tools(SimpleNamespace(launch=lambda **kw: pytest.fail("launched")), cancel=cancel)
    assert result.status == "cancelled"


def test_provider_exception_is_not_relabelled_as_daemon_failure(monkeypatch):
    monkeypatch.setattr(shared_mcp, "start_daemon_from_app", lambda: {"ok": True})
    monkeypatch.setattr(shared_mcp, "_daemon_answers", lambda **kw: True)
    def launch(**kwargs):
        raise ValueError("provider bug")
    with pytest.raises(ValueError, match="provider bug"):
        readiness.launch_with_ready_tools(SimpleNamespace(launch=launch))


def test_probe_receives_remaining_deadline(monkeypatch):
    now, probes = [0.0], []
    monkeypatch.setattr(readiness.time, "monotonic", lambda: now[0])
    def start():
        now[0] = 19.5
        return {"ok": True}
    def probe(timeout_s):
        probes.append(timeout_s)
        return True
    monkeypatch.setattr(shared_mcp, "start_daemon_from_app", start)
    monkeypatch.setattr(shared_mcp, "_daemon_answers", probe)
    assert readiness.launch_with_ready_tools(SimpleNamespace(launch=lambda **kw: "done")) == "done"
    assert probes == [0.5]


def test_runner_uses_the_gate_for_its_only_provider_launch():
    import ast
    from pathlib import Path
    tree = ast.parse(Path(readiness.__file__).with_name("runner.py").read_text(encoding="utf-8"))
    calls = [node.func for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert sum(isinstance(f, ast.Name) and f.id == "launch_with_ready_tools" for f in calls) == 1
    assert not any(isinstance(f, ast.Attribute) and f.attr == "launch" for f in calls)
