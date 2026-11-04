"""The tool server confirms a steer lease only after the message is in the tool result."""
import asyncio

import pytest

from backend.panel import rpc
from backend.server import ProtectedFastMCP
from backend.workspace import ai_ignore, identity
from frontend.ui_web import steer
from frontend.ui_web.agent_turns.testing import patch_turns


@pytest.fixture
def server(monkeypatch):
    from backend.agent import chat_title, verify_evidence

    now = [100.0]
    monkeypatch.setattr(steer, "_now", lambda: now[0])
    patch_turns(monkeypatch, "is_agent_running", lambda cid: True)
    monkeypatch.setattr(ai_ignore, "require_safe_tool", lambda _name: None)
    monkeypatch.setattr(rpc, "wait_for_question_answers", lambda _conv_id: None)
    monkeypatch.setattr(chat_title, "require_self_name", lambda *_args: None)
    monkeypatch.setattr(verify_evidence, "record_result", lambda *a: None)
    calls, fail = [], {"ack": False}

    def fake_panel(method, params=None, *, timeout=30.0):
        # The panel's steer RPCs (panel_httpd._steer_rpc), answered in-process.
        calls.append((method, timeout))
        if method == "steer_take":
            return steer.take(params["conv_id"])
        if method == "steer_ack":
            if fail["ack"]:
                return {"error": "timeout", "timed_out": True}
            steer.ack(params["conv_id"], params["lease_id"])
            return {"ok": True}
        raise AssertionError(method)

    monkeypatch.setattr(rpc, "panel_rpc", fake_panel)
    mcp = ProtectedFastMCP("steer-ack")

    @mcp.tool()
    def read_thing() -> str:
        return "thing"

    events = []
    steer.bind_run("c", events.append)

    def call() -> str:
        token = identity.bind(identity.RunContext(run_id="r", conv_id="c"))
        try:
            rows = asyncio.run(mcp.call_tool("read_thing", {}))
        finally:
            identity.reset(token)
        content = rows[0] if isinstance(rows, tuple) else rows
        return "\n".join(getattr(c, "text", "") for c in content)

    yield type("S", (), {"call": staticmethod(call), "calls": calls, "fail": fail, "now": now, "events": events})
    steer.end_run("c")


def test_message_is_attached_once_and_acked_after_the_result(server):
    steer.post("c", "q1", "use blue")
    first = server.call()
    assert "use blue" in first and "thing" in first
    assert [m for m, _ in server.calls] == ["steer_take", "steer_ack"]
    assert dict(server.calls)["steer_take"] >= 10.0  # a panel that answers in 8 s is still in time
    assert [e["steer_id"] for e in server.events] == ["q1"]
    assert "use blue" not in server.call()  # never twice


def test_failed_ack_brings_the_message_back_on_a_later_call(server):
    steer.post("c", "q1", "stop after this file")
    server.fail["ack"] = True
    assert "stop after this file" in server.call()
    assert server.events == []  # not confirmed
    server.fail["ack"] = False
    assert "stop after this file" not in server.call()  # the lease is still open
    server.now[0] += steer.LEASE_S + 0.1
    assert "stop after this file" in server.call()  # the lease ran out: carried again, not lost
    assert [e["steer_id"] for e in server.events] == ["q1"]


def test_no_message_no_ack(server):
    assert server.call() == "thing"
    assert [m for m, _ in server.calls] == ["steer_take"]
