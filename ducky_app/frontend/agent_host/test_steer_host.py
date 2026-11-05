"""D20: a running chat's steer and question checks are answered by its agent host."""

from __future__ import annotations

import json
import threading

import pytest

from frontend.agent_host import steer_host, turns
from frontend.ui_web import steer


class _Recorder:
    def __init__(self, alive=True):
        self.sent = []
        self._alive = alive

    def send(self, op, **fields):
        self.sent.append((op, fields))
        return True

    def alive(self):
        return self._alive


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(steer, "_inbox", {})
    monkeypatch.setattr(steer, "_run_push", {})
    monkeypatch.setattr(steer, "_forwarded", {})
    monkeypatch.setattr(steer_host, "_open_questions", set())
    monkeypatch.setattr(steer_host, "_in_host", False)  # enable() in a test must not outlive it


def test_the_hosts_pipe_answers_steer_and_question_checks(monkeypatch):
    from backend.panel import rpc

    steer_host.enable()
    env = steer_host.mcp_env()
    pipe, key = env[rpc.HOST_PIPE_ENV], bytes.fromhex(env[rpc.HOST_KEY_ENV])
    steer_host.post("c1", "s1", "use blue")  # forwarded before the runner bound its push
    lease = rpc.host_rpc(pipe, key, "steer_take", {"conv_id": "c1"}, 5)
    assert lease["texts"] == ["use blue"] and lease["steer_ids"] == ["s1"]
    assert rpc.host_rpc(pipe, key, "steer_ack", {"conv_id": "c1", "lease_id": lease["lease_id"]}, 5) == {"ok": True}
    assert steer.take("c1")["texts"] == []

    monkeypatch.setattr(steer_host, "GATE_WAIT_S", 0.2)
    steer_host.set_question("c1", True)
    assert rpc.host_rpc(pipe, key, "question_gate", {"conv_id": "c1"}, 5) == {"blocked": True}
    threading.Timer(0.05, steer_host.set_question, args=("c1", False)).start()
    assert rpc.host_rpc(pipe, key, "question_gate", {"conv_id": "c1"}, 5) == {"blocked": False}


def test_the_tool_server_asks_the_host_and_without_a_pipe_the_app(monkeypatch):
    from backend.panel import rpc

    steer_host.enable()
    env = steer_host.mcp_env()
    app_calls = []
    monkeypatch.setattr(rpc, "panel_rpc", lambda method, params, timeout=0: app_calls.append(method) or {})
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    steer_host.post("c2", "s2", "stop after tests")
    lease_id, texts = rpc.take_steer_messages("c2")
    assert texts == ["stop after tests"] and app_calls == []
    rpc.ack_steer("c2", lease_id)
    assert app_calls == []
    for name in env:
        monkeypatch.delenv(name)
    rpc.take_steer_messages("c2")
    assert app_calls == ["steer_take"]


def test_the_app_forwards_posts_for_a_host_owned_chat(monkeypatch):
    from frontend.ui_web.agent_turns.testing import patch_turns

    host = _Recorder()
    monkeypatch.setattr(turns, "owns", lambda cid: host if cid == "c3" else None)
    patch_turns(monkeypatch, "is_agent_running", lambda cid: True)
    assert steer.post("c3", "s3", "also add a test")
    assert host.sent == [("steer_post", {"conv_id": "c3", "steer_id": "s3", "text": "also add a test"})]
    assert steer._inbox == {} and steer.has_queued("c3")
    steer.delivered("c3", "s3")  # the host's user_steered event arrived
    assert not steer.has_queued("c3")

    assert steer.post("c3", "s4", "one more")
    steer.host_turn_ended("c3", reclaim=True)  # the host died: the message comes back here
    assert steer.take("c3")["texts"] == ["one more"]

    steer.post("c3", "s4", "")  # taken back out of the composer queue
    assert host.sent[-1] == ("steer_post", {"conv_id": "c3", "steer_id": "s4", "text": ""})
    assert steer.post("other", "s5", "local") and steer.take("other")["texts"] == ["local"]


def test_question_state_follows_the_app_into_the_host(monkeypatch):
    from frontend.ui_web import ui_rpc

    host = _Recorder()
    monkeypatch.setattr(turns, "owns", lambda cid: host if cid == "c4" else None)
    rid, _ = ui_rpc.submit("ask_user", {"conv_id": "c4", "questions": [{"id": "q"}]})
    try:
        assert host.sent[-1] == ("question_answer", {"conv_id": "c4", "open": True})
        assert ui_rpc.respond(rid, {"ok": True, "answers": {"q": {"text": "yes"}}})
        assert host.sent[-1] == ("question_answer", {"conv_id": "c4", "open": False})
    finally:
        ui_rpc.cancel(rid)
    ui_rpc.submit("navigate", {"conv_id": "c4"})  # not a question: the host hears nothing
    assert len(host.sent) == 2


def test_host_frames_drive_the_hosts_state():
    turns.on_frame(None, {"op": "steer_post", "conv_id": "c5", "steer_id": "s1", "text": "hi"})
    assert steer.take("c5")["texts"] == ["hi"]
    turns.on_frame(None, {"op": "question_answer", "conv_id": "c5", "open": True})
    assert steer_host.question_gate("c5", 0) is True
    turns.on_frame(None, {"op": "question_answer", "conv_id": "c5", "open": False})
    assert steer_host.question_gate("c5", 0) is False


def test_the_runner_puts_the_hosts_pipe_in_the_uefn_servers_env(tmp_path, monkeypatch):
    from backend.agent.coding_agents import runner

    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"uefn": {"command": "x", "env": {"DUCKY_CONV_ID": "c6"}},
                                               "other": {"command": "y"}}}))
    monkeypatch.setattr(steer_host, "mcp_env", lambda: {})
    runner._add_agent_host_env(path)
    assert "DUCKY_AGENT_HOST_PIPE" not in path.read_text()  # not in a host: the app answers
    monkeypatch.setattr(steer_host, "mcp_env", lambda: {"DUCKY_AGENT_HOST_PIPE": "p", "DUCKY_AGENT_HOST_KEY": "k"})
    runner._add_agent_host_env(path)
    servers = json.loads(path.read_text())["mcpServers"]
    assert servers["uefn"]["env"] == {"DUCKY_CONV_ID": "c6", "DUCKY_AGENT_HOST_PIPE": "p", "DUCKY_AGENT_HOST_KEY": "k"}
    assert "env" not in servers["other"]


def test_the_hosts_pipe_answers_a_memory_report_for_the_host_process():
    """ducky_memory_report only measured the app; an idle host held 1.12 GB (Nov 5 2026)."""
    import os

    from backend.panel import rpc

    steer_host.enable()
    env = steer_host.mcp_env()
    pipe, key = env[rpc.HOST_PIPE_ENV], bytes.fromhex(env[rpc.HOST_KEY_ENV])
    report = rpc.host_rpc(pipe, key, "memory_report", {"top": 3, "walk_budget_s": 1, "collect": False}, 60)
    assert report["process"]["pid"] == os.getpid()  # measured here, in the host
    assert {"native", "gc", "containers", "took_s"} <= set(report)
    assert len(report["gc"]["types"]) <= 3
