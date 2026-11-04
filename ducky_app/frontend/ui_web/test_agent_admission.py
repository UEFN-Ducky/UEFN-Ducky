"""Admission tokens fence preparation, worker startup and stale finalizers."""
from types import SimpleNamespace
import threading

import pytest
from frontend.settings import PanelSettings
from frontend.ui_web.agent_turns import routing, state
from frontend.ui_web.agent_turns.testing import patch_turns


@pytest.fixture
def admissions(monkeypatch):
    patch_turns(monkeypatch, "_admissions", {})
    patch_turns(monkeypatch, "_sessions", {})
    patch_turns(monkeypatch, "_in_bridge_process", lambda: False)
    patch_turns(monkeypatch, "_pending_agent_messages", {})
    return state


def test_preparation_is_busy_and_prevents_briefing(admissions):
    token = state._reserve_admission("c")
    assert token and state.is_agent_running("c")
    assert state._reserve_admission("c") is None
    called = []
    assert not state.append_if_idle("c", lambda: called.append(True))
    assert called == []
    state._release_admission("c", token)
    assert state.append_if_idle("c", lambda: called.append(True))
    assert called == [True]


def test_reserved_without_worker_waits_for_matching_release(admissions):
    token = state._reserve_admission("reserved")
    assert not state.wait_for_idle("reserved", 0.01)
    state._release_admission("reserved", token)
    assert state.wait_for_idle("reserved", 0.01)


def test_stale_finalizer_cannot_release_new_token(admissions):
    old = state._reserve_admission("c")
    state._release_admission("c", old)
    new = state._reserve_admission("c")
    state._release_admission("c", old)
    assert state.is_agent_running("c")
    state._release_admission("c", new)
    assert not state.is_agent_running("c")


def test_append_and_reservation_share_actual_lock(admissions):
    entered, finish = threading.Event(), threading.Event()
    outcomes = []
    def append():
        entered.set()
        assert finish.wait(2)
    t = threading.Thread(target=lambda: state.append_if_idle("c", append))
    t.start()
    assert entered.wait(2)
    r = threading.Thread(target=lambda: outcomes.append(state._reserve_admission("c")))
    r.start()
    assert not outcomes
    finish.set()
    t.join(2)
    r.join(2)
    assert len(outcomes) == 1 and outcomes[0]


def test_failed_preparation_releases_token(admissions, monkeypatch):
    patch_turns(monkeypatch, "load_conversation", lambda cid: SimpleNamespace(id=cid))
    def fail():
        assert state.is_agent_running("c")
        raise RuntimeError("preparation failed")
    monkeypatch.setattr(PanelSettings, "load", fail)
    with pytest.raises(RuntimeError, match="preparation failed"):
        routing.run_message("c", "hi", "agent", "model", _local=True)
    assert not state.is_agent_running("c")


def test_reserved_turn_queues_instead_of_cancelling(admissions, monkeypatch):
    token = state._reserve_admission("c")
    patch_turns(monkeypatch, "load_conversation", lambda cid: SimpleNamespace(id=cid))
    assert routing.run_message("c", "queued", "agent", "model", queue_if_busy=True, _local=True) == "queued"
    assert state._admissions["c"] is token
    assert state._pending_agent_messages["c"] == ["queued"]


def test_person_send_into_running_turn_queues_and_steers(admissions, monkeypatch):
    from backend.agent import a2a_broker
    from frontend.ui_web import steer

    monkeypatch.setattr(a2a_broker, "_retry_queue_later", lambda cid: None)
    monkeypatch.setattr(steer, "_inbox", {})
    cancels, pushed = [], []
    patch_turns(monkeypatch, "cancel_agent", lambda cid=None, **kw: cancels.append(cid))
    token = state._reserve_admission("c")
    assert routing.run_message("c", "also do X", "agent", "model", push=pushed.append, _local=True) == "queued"
    assert cancels == []  # the running turn is never stopped
    assert state._admissions["c"] is token
    assert state._pending_agent_messages["c"] == ["also do X"]
    assert [m["text"] for m in steer._inbox["c"]] == ["also do X"]
    assert not any("already running" in str(e.get("text")) for e in pushed)


def test_person_send_while_idle_starts_a_turn(admissions, monkeypatch):
    started = []
    patch_turns(monkeypatch, "load_conversation", lambda cid: started.append(cid) or None)
    assert routing.run_message("c", "hi", "agent", "model", push=lambda e: None, _local=True) == ""
    assert started == ["c"]  # reached the turn body, nothing queued
    assert "c" not in state._pending_agent_messages


def test_a_second_stop_kills_at_once(admissions, monkeypatch):
    from backend.agent.coding_agents import proc_exec
    from backend.agent import a2a_broker

    monkeypatch.setattr(a2a_broker, "on_agent_cancelled_by_user", lambda cid: None)
    killed = []
    monkeypatch.setattr(proc_exec, "terminate_conv_process", lambda cid: killed.append(cid) or True)
    session = state.AgentSession("c")
    state._sessions["c"] = session
    state._cancel_here("c")  # first Stop: the turn is asked to stop
    assert session._cancel.is_set() and killed == []
    state._cancel_here("c")  # the person insists
    assert killed == ["c"]


def test_reserved_turn_is_listed_before_session_exists(admissions):
    state._reserve_admission("c")
    assert state.list_running_agents() == ["c"]


def test_cancelled_start_never_overwrites_new_session(admissions, monkeypatch):
    session = state.AgentSession()
    old = state._reserve_admission("c")
    monkeypatch.setattr(state._admission_context, "current", ("c", old), raising=False)
    session.prepare_run("old")
    state._release_admission("c", old)
    new = state._reserve_admission("c")
    state._admission_context.current = ("c", new)
    session.prepare_run("new")
    new_cancel = session._cancel
    state._admission_context.current = ("c", old)
    with pytest.raises(state._AdmissionCancelled):
        session.start(lambda: pytest.fail("stale target ran"), "old")
    assert session.run_id == "new"
    assert session._cancel is new_cancel and not new_cancel.is_set()
    assert state._admissions["c"] is new


def test_start_failure_releases_its_token(admissions, monkeypatch):
    from frontend.ui_web.live_agent_runs import get_live_run_ids
    token = state._reserve_admission("c")
    monkeypatch.setattr(state._admission_context, "current", ("c", token), raising=False)
    session = state.AgentSession()
    session.prepare_run("failed-start")
    class FailedThread:
        def __init__(self, **kwargs): pass
        def start(self): raise RuntimeError("start failed")
        def is_alive(self): return False
    monkeypatch.setattr(threading, "Thread", FailedThread)
    with pytest.raises(RuntimeError, match="start failed"):
        session.start(lambda: None, "failed-start")
    assert not state.is_agent_running("c")
    assert "failed-start" not in get_live_run_ids()


def test_delayed_old_worker_cannot_run_or_release_new_token(admissions, monkeypatch):
    callbacks = []
    class DelayedThread:
        def __init__(self, *, target, **kwargs): callbacks.append(target)
        def start(self): pass
        def is_alive(self): return False
    monkeypatch.setattr(threading, "Thread", DelayedThread)
    session = state.AgentSession()
    old = state._reserve_admission("c")
    monkeypatch.setattr(state._admission_context, "current", ("c", old), raising=False)
    session.prepare_run("old")
    session.start(lambda: pytest.fail("cancelled target ran"), "old")
    state._release_admission("c", old)
    new = state._reserve_admission("c")
    callbacks[0]()
    assert state._admissions["c"] is new
