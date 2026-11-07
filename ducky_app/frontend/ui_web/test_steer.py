"""Send now into a running turn: held until a tool call takes it, never a Stop."""

from __future__ import annotations

import time

from backend.agent.coding_agents.runner import merge_steered_blocks
from backend.server import _with_steer
from frontend.ui_web import steer
from frontend.ui_web.agent_turns.testing import patch_turns


def test_post_take_and_end(monkeypatch):
    patch_turns(monkeypatch, "is_agent_running", lambda cid: cid == "busy")
    events = []
    steer.bind_run("busy", events.append)
    try:
        assert steer.post("idle", "q0", "hi") is False  # no turn: the composer sends it normally
        assert steer.post("busy", "q1", "use blue") is True
        assert steer.post("busy", "q1", "use blue") is True  # Send now twice: held once
        assert steer.post("busy", "q2", "and red") is True
        steer.post("busy", "q2", "")  # deleted from the queue before a tool call took it
        lease = steer.take("busy")
        assert lease["texts"] == ["use blue"] and lease["steer_ids"] == ["q1"] and lease["lease_id"]
        assert events == []  # not delivered until the tool server confirms
        assert steer.ack("busy", lease["lease_id"]) == ["use blue"]
        assert events == [{"type": "user_steered", "conv_id": "busy", "steer_id": "q1", "text": "use blue"}]
        assert steer.ack("busy", lease["lease_id"]) == []  # pushed once
        assert steer.take("busy")["texts"] == []
        steer.post("busy", "q3", "late")
    finally:
        steer.end_run("busy")
    assert steer.take("busy")["texts"] == []  # the turn ended: its queue runs it next instead


def _clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(steer, "_now", lambda: now[0])
    patch_turns(monkeypatch, "is_agent_running", lambda cid: True)
    return now


def test_unconfirmed_lease_comes_back(monkeypatch):
    now = _clock(monkeypatch)
    events = []
    steer.bind_run("c", events.append)
    try:
        steer.post("c", "q1", "also update the README")
        first = steer.take("c")
        assert first["texts"] == ["also update the README"]
        steer.post("c", "q2", "and the docs")
        assert steer.take("c")["texts"] == []  # the first lease is still open
        now[0] += 11.9
        assert steer.take("c")["texts"] == []
        now[0] += 0.2  # 12 s without an ack: back in the queue
        second = steer.take("c")
        assert second["texts"] == ["also update the README", "and the docs"]
        assert steer.ack("c", first["lease_id"]) == []  # the stale lease confirms nothing
        assert steer.ack("c", second["lease_id"]) == ["also update the README", "and the docs"]
        assert [e["steer_id"] for e in events] == ["q1", "q2"]
    finally:
        steer.end_run("c")


def test_has_queued(monkeypatch):
    now = _clock(monkeypatch)
    try:
        assert steer.has_queued("c") is False
        steer.post("c", "q1", "x")
        assert steer.has_queued("c") is True
        steer.take("c")
        assert steer.has_queued("c") is False  # leased items don't count
        now[0] += steer.LEASE_S
        assert steer.has_queued("c") is True  # lease expired back to queued
        t = time.perf_counter()
        for _ in range(10_000):
            steer.has_queued("c")
        assert time.perf_counter() - t < 0.5  # ~10 ms here; loose for slow CI
    finally:
        steer.end_run("c")


def test_taken_back_drops_leased_copy(monkeypatch):
    _clock(monkeypatch)
    try:
        steer.post("c", "q1", "x")
        lease = steer.take("c")
        steer.post("c", "q1", "")
        assert steer.ack("c", lease["lease_id"]) == []
    finally:
        steer.end_run("c")


def test_stop_after_a_tool_took_the_queued_prompt_says_delivered(monkeypatch):
    import frontend.ui_web.panel_api  # noqa: F401 - loads panel_chats the way the app does
    from frontend.ui_web.panel_chats.send import steer_message

    _clock(monkeypatch)
    steer.bind_run("stop", lambda e: None)
    steer.post("stop", "q1", "tahst aboe 30kthat uefn allows")
    steer.post("stop", "q2", "never taken")
    lease = steer.take("stop")
    steer.post("stop", "q2", "")  # q2 was edited back out; only q1 rode on the tool result
    steer.ack("stop", lease["lease_id"])
    steer.end_run("stop")  # Stop: the turn is gone, the composer drains its queue
    assert steer_message(None, "stop", "q1", "") == {"ok": False, "delivered": True}
    assert steer_message(None, "stop", "q2", "") == {"ok": False, "delivered": False}
    steer.delivered("stop", "h1")  # an agent host's user_steered event reached the app
    assert steer.was_delivered("stop", "h1") and not steer.was_delivered("other", "q1")


def test_steer_note_rides_on_every_result_shape():
    from mcp.types import CallToolResult, TextContent

    text = TextContent(type="text", text="ok")
    content, structured = _with_steer(([text], {"a": 1}), ["stop using red"])
    assert structured == {"a": 1} and "stop using red" in content[-1].text
    assert "stop using red" in _with_steer([text], ["stop using red"])[-1].text
    both, kept = _with_steer({"a": 1}, ["x"])
    assert kept == {"a": 1} and len(both) == 2
    protocol = _with_steer(CallToolResult(content=[text]), ["x"])
    assert protocol.content[-1].text.endswith("x")


def test_merge_puts_message_after_its_tool():
    final = [
        {"type": "text", "text": "a"},
        {"type": "tool_call", "id": "t1"},
        {"type": "tool_call", "id": "t2"},
    ]
    live = [
        {"type": "tool_call", "id": "t1"},
        {"type": "user", "text": "one", "after": "t1"},
        {"type": "user", "text": "two", "after": "t1"},
        {"type": "user", "text": "lost", "after": "gone"},
    ]
    merge_steered_blocks(final, live)
    assert [b.get("id") or b.get("text") for b in final] == ["a", "t1", "one", "two", "t2", "lost"]
