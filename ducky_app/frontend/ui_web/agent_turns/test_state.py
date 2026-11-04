"""agent_turns.state: what a chat's live turn is doing, read without waiting, and run admission."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from frontend.ui_web.agent_turns import state


@pytest.fixture
def live(monkeypatch):
    alive = {"yes": True}
    session = SimpleNamespace(run_id="run-1", _thread=SimpleNamespace(is_alive=lambda: alive["yes"]))
    monkeypatch.setitem(state._sessions, "c1", session)
    monkeypatch.setattr(state, "_turn_activity", {})
    return alive


def test_nothing_without_a_turn(monkeypatch):
    monkeypatch.setattr(state, "_turn_activity", {})
    assert state.running_tool("nope") is None and state.last_output_age_s("nope") is None


def test_the_open_tool_and_the_last_output(live):
    state._note_activity("c1", "run-1", {"type": "text", "text": "hi"})
    assert state.running_tool("c1") is None
    assert 0 <= state.last_output_age_s("c1") < 1
    t0 = time.time()
    state._note_activity("c1", "run-1", {"type": "tool", "tool": {"id": "t1", "name": "ducky_terminal_run",
                                                                   "status": "pending"}})
    got = state.running_tool("c1")
    assert got["name"] == "ducky_terminal_run" and got["started_at"] >= t0
    state._note_activity("c1", "run-1", {"type": "tool_done", "tool": {"id": "t1", "name": "ducky_terminal_run"}})
    assert state.running_tool("c1") is None


def test_a_finished_or_replaced_run_reads_as_none(live):
    state._note_activity("c1", "run-1", {"type": "tool", "tool": {"id": "t1", "name": "x", "status": "pending"}})
    live["yes"] = False
    assert state.running_tool("c1") is None and state.last_output_age_s("c1") is None
    live["yes"] = True
    state._sessions["c1"].run_id = "run-2"
    assert state.running_tool("c1") is None


def test_an_admitted_chat_runs_until_released(monkeypatch):
    monkeypatch.setattr(state, "_admissions", {})
    monkeypatch.setattr(state, "_sessions", {})
    monkeypatch.setattr(state, "_panel_runs", lambda *a, **k: None)
    token = state._reserve_admission("c")
    assert token is not None and state.is_agent_running("c")
    assert state._reserve_admission("c") is None
    assert not state.append_if_idle("c", lambda: True)
    state._release_admission("c", token)
    assert not state.is_agent_running("c") and state.wait_for_idle("c", 0.01)
    with pytest.raises(state._AdmissionCancelled):
        state._check_admission("c", token)


def test_a_stop_drops_the_chats_queued_messages(monkeypatch):
    monkeypatch.setattr(state, "_admissions", {})
    monkeypatch.setattr(state, "_sessions", {"c": state.AgentSession("c")})
    monkeypatch.setattr(state, "_panel_runs", lambda *a, **k: None)
    monkeypatch.setitem(state._pending_agent_messages, "c", ["later"])
    state.cancel_agent("c", by_user=False)
    assert "c" not in state._pending_agent_messages
    assert state._sessions["c"]._cancel.is_set() and "c" not in state._admissions


def test_a_linked_child_reports_its_end_to_the_parent(monkeypatch):
    monkeypatch.setattr(state, "_linked_parents", {"kid": "lead"})
    seen: list[dict] = []
    push = state._wrap_push_for_linked_child(seen.append, parent_conv_id="lead", child_conv_id="kid",
                                             child_title="Kid")
    push({"type": "agent_stopped", "conv_id": "kid", "reason": "cancelled"})
    assert seen[-1] == {"type": "linked_agent", "parent_conv_id": "lead", "child_conv_id": "kid",
                        "title": "Kid", "status": "cancelled"}
    assert "kid" not in state._linked_parents
    assert state._wrap_push_for_linked_child(seen.append, parent_conv_id=None, child_conv_id="kid",
                                             child_title="Kid") == seen.append


def test_session_start_runs_the_target_and_forgets_the_session(monkeypatch):
    monkeypatch.setattr(state, "_sessions", {})
    session = state._session("c")
    ran = threading.Event()
    session.start(ran.set, "run-1")
    assert ran.wait(2) and state.wait_for_idle("c", 2)
    assert "c" not in state._sessions
