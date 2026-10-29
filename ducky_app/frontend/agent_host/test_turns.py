"""Turns in the host: a stub turn runs through a real pipe pair and ends; Stop and a dead host end it."""

from __future__ import annotations

import os
import threading
import time
import uuid
from types import SimpleNamespace

import pytest

from frontend.agent_host import protocol, turns


class _Host:
    def __init__(self, channel):
        self.channel = channel
        self.ready = threading.Event()
        self.ready.set()

    def alive(self):
        return not self.channel.closed

    def send(self, op, **fields):
        return self.channel.send(op, **fields)


@pytest.fixture
def piped(monkeypatch):
    """An app channel and a host channel over a real named pipe, the host side running turns.on_frame."""
    tag = f"t-{uuid.uuid4().hex[:8]}"
    key = os.urandom(16)
    listener = protocol.listen(tag, key)
    box = []
    t = threading.Thread(target=lambda: box.append(protocol.connect_app(
        protocol.pipe_name(tag), key, lambda frame: turns.on_frame(box[0], frame), heartbeat_s=0)), daemon=True)
    t.start()
    holder = {}
    app = protocol.accept_host(listener, lambda frame: turns.on_app_frame(holder["host"], frame), dead_after_s=0)
    t.join(5)
    listener.close()
    host = holder["host"] = _Host(app)
    released = []
    monkeypatch.setattr(turns, "_supervisor", lambda: SimpleNamespace(assign=lambda cid: host, release=released.append))
    monkeypatch.setattr(turns, "_sender", lambda ch: lambda ev: ch.send("event", conv_id=ev["conv_id"], events=[ev]))
    from backend.agent.coding_agents import runner
    from frontend import settings
    from frontend.ui_web import project_chats

    monkeypatch.setattr(runner, "on_saved", None)  # the host side sets it per channel
    from frontend.agent_host import steer_host

    monkeypatch.setattr(steer_host, "_in_host", False)
    monkeypatch.setattr(settings, "PanelSettings", SimpleNamespace(load=lambda: SimpleNamespace(uefn_project_root="")))
    monkeypatch.setattr(settings, "apply_workspace_env", lambda root: None)
    monkeypatch.setattr(project_chats, "load_conversation", lambda cid: SimpleNamespace(id=cid, messages=[]))
    yield SimpleNamespace(app=app, host_channel=box[0], released=released)
    app.close()
    box[0].close()


def _stub_runner(monkeypatch, body):
    from backend.agent.coding_agents import runner

    monkeypatch.setattr(runner, "run_coding_agent_message", body)


def test_a_stub_turn_streams_its_events_and_ends(piped, monkeypatch):
    def stub(conv, text, *, model, push, run_id, cancel, mode):
        assert (conv.id, text, model, mode) == ("c1", "hi", "claude-haiku-5-5", "agent")
        push({"type": "status", "text": "Starting"})
        push({"type": "text_delta", "text": "Hello"})
        push({"type": "agent_stopped", "reason": "done"})

    _stub_runner(monkeypatch, stub)
    got = []
    out = turns.run_in_host("c1", "r1", "hi", model="claude-haiku-5-5", mode="agent", push=got.append,
                            cancel=threading.Event())
    assert out == ""
    assert [e["type"] for e in got] == ["status", "text_delta", "agent_stopped"]
    assert all(e["conv_id"] == "c1" and e["run_id"] == "r1" for e in got)
    assert piped.released == ["c1"] and turns.live_turns() == {}


def test_a_host_turns_tool_calls_count_in_the_app(piped, monkeypatch):
    from frontend import usage_stats

    def stub(conv, text, *, model, push, run_id, cancel, mode):
        push({"type": "tool", "tool": {"id": "t1", "name": "Read"}})
        push({"type": "tool", "tool": {"id": "t2", "name": "Edit"}})
        push({"type": "agent_stopped", "reason": "done"})

    _stub_runner(monkeypatch, stub)
    counted = []
    monkeypatch.setattr(usage_stats, "turn_tool", counted.append)
    out = turns.run_in_host("c7", "r7", "hi", model="m", mode="agent", push=lambda e: None,
                            cancel=threading.Event())
    assert out == "" and counted == ["r7", "r7"]


def test_stop_reaches_the_host_and_ends_the_turn(piped, monkeypatch):
    started = threading.Event()

    def stub(conv, text, *, model, push, run_id, cancel, mode):
        started.set()
        assert cancel.wait(5), "the host never heard Stop"
        push({"type": "agent_stopped", "reason": "cancelled"})

    _stub_runner(monkeypatch, stub)
    cancel, got = threading.Event(), []
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("out", turns.run_in_host(
        "c2", "r2", "go", model="m", mode="agent", push=got.append, cancel=cancel)))
    t.start()
    assert started.wait(5)
    assert turns.live_turns() == {"c2": "r2"} and turns.owns("c2") is not None
    cancel.set()
    t.join(5)
    assert result["out"] == "" and got[-1]["reason"] == "cancelled"


def test_a_runner_error_comes_back_as_the_turns_error(piped, monkeypatch):
    def stub(conv, text, **kw):
        raise ValueError("CLI missing")

    _stub_runner(monkeypatch, stub)
    from backend.agent.coding_agents import runner

    monkeypatch.setattr(runner, "checkpoint_coding_turn", lambda *a, **kw: None)
    out = turns.run_in_host("c3", "r3", "x", model="m", mode="agent", push=lambda e: None, cancel=threading.Event())
    assert out == "CLI missing"


def test_a_dead_host_ends_the_turn_with_an_error(piped, monkeypatch):
    started = threading.Event()
    _stub_runner(monkeypatch, lambda conv, text, **kw: started.set() or threading.Event().wait(10))
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("out", turns.run_in_host(
        "c4", "r4", "x", model="m", mode="agent", push=lambda e: None, cancel=threading.Event())))
    t.start()
    assert started.wait(5)
    piped.host_channel.close("crash")
    t.join(5)
    assert not t.is_alive() and result["out"] == turns.INTERRUPTED


def test_a_dead_hosts_turns_are_interrupted_and_others_untouched(monkeypatch):
    closed: list[list[str]] = []
    monkeypatch.setattr(turns, "_close_runs", closed.append)

    class _Seat:
        def __init__(self, slot):
            self.slot = slot
            self.ready = threading.Event()
            self.ready.set()

        def alive(self):
            return True

        def send(self, op, **fields):
            return True

        def __hash__(self):
            return id(self)

    dead, other = _Seat(0), _Seat(1)
    seats = {"a": dead, "b": dead, "c": other}
    monkeypatch.setattr(turns, "_supervisor", lambda: SimpleNamespace(assign=seats.get, release=lambda cid: None))
    results: dict[str, str] = {}
    threads = [threading.Thread(target=lambda cid=cid: results.setdefault(cid, turns.run_in_host(
        cid, f"r-{cid}", "x", model="m", mode="agent", push=lambda e: None, cancel=threading.Event())))
        for cid in seats]
    for t in threads:
        t.start()
    deadline = threading.Event()
    while len(turns.live_turns()) < 3 and not deadline.wait(0.01):
        pass
    assert sorted(turns.mark_host_dead(0)) == ["a", "b"]
    for t in threads[:2]:
        t.join(5)
    assert results == {"a": turns.INTERRUPTED, "b": turns.INTERRUPTED}
    assert sorted(closed[0]) == ["r-a", "r-b"]  # their ledger rows become interrupted
    assert turns.live_turns() == {"c": "r-c"}  # the turn on the other host runs on
    turns.on_app_frame(other, {"op": "turn_ended", "run_id": "r-c", "error": ""})
    threads[2].join(5)
    assert results["c"] == ""
    assert turns.HostTurnError(turns.INTERRUPTED).interrupted and not turns.HostTurnError("x").interrupted


def test_no_host_means_the_app_runs_the_turn(monkeypatch):
    def broken():
        raise OSError("no exe")

    monkeypatch.setattr(turns, "_supervisor", broken)
    assert turns.run_in_host("c5", "r5", "x", model="m", mode="agent", push=lambda e: None,
                             cancel=threading.Event()) is None


def test_a_host_that_died_before_connecting_does_not_hold_the_turn(monkeypatch):
    class _Dead:
        ready = threading.Event()

        def alive(self):
            return False

    released = []
    monkeypatch.setattr(turns, "_supervisor", lambda: SimpleNamespace(assign=lambda cid: _Dead(), release=released.append))
    started = time.monotonic()
    assert turns.run_in_host("c6", "r6", "x", model="m", mode="agent", push=lambda e: None,
                             cancel=threading.Event()) is None
    assert time.monotonic() - started < 2 and released == ["c6"]  # was READY_S (30 s) per turn


def test_host_switch(monkeypatch):
    monkeypatch.setenv("DUCKY_AGENT_HOST", "0")
    assert not turns.enabled()
    monkeypatch.setenv("DUCKY_AGENT_HOST", "1")
    assert turns.enabled()
    monkeypatch.delenv("DUCKY_AGENT_HOST")
    assert not turns.enabled()  # under pytest the app runs turns itself unless asked
