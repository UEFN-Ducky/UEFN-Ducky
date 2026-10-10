"""Admission tokens fence preparation, worker startup and stale finalizers."""
from types import SimpleNamespace
import threading

import pytest
from frontend.ui_web import agent_modes as am


@pytest.fixture
def admissions(monkeypatch):
    monkeypatch.setattr(am, "_admissions", {}, raising=False)
    monkeypatch.setattr(am, "_sessions", {})
    monkeypatch.setattr(am, "_in_bridge_process", lambda: False)
    monkeypatch.setattr(am, "_pending_agent_messages", {})
    return am


def test_preparation_is_busy_and_prevents_briefing(admissions):
    token = am._reserve_admission("c")
    assert token and am.is_agent_running("c")
    assert am._reserve_admission("c") is None
    called = []
    assert not am.append_if_idle("c", lambda: called.append(True))
    assert called == []
    am._release_admission("c", token)
    assert am.append_if_idle("c", lambda: called.append(True))
    assert called == [True]


def test_reserved_without_worker_waits_for_matching_release(admissions):
    token = am._reserve_admission("reserved")
    assert not am.wait_for_idle("reserved", 0.01)
    am._release_admission("reserved", token)
    assert am.wait_for_idle("reserved", 0.01)


def test_stale_finalizer_cannot_release_new_token(admissions):
    old = am._reserve_admission("c")
    am._release_admission("c", old)
    new = am._reserve_admission("c")
    am._release_admission("c", old)
    assert am.is_agent_running("c")
    am._release_admission("c", new)
    assert not am.is_agent_running("c")


def test_append_and_reservation_share_actual_lock(admissions):
    entered, finish = threading.Event(), threading.Event()
    outcomes = []
    def append():
        entered.set()
        assert finish.wait(2)
    t = threading.Thread(target=lambda: am.append_if_idle("c", append))
    t.start()
    assert entered.wait(2)
    r = threading.Thread(target=lambda: outcomes.append(am._reserve_admission("c")))
    r.start()
    assert not outcomes
    finish.set()
    t.join(2); r.join(2)
    assert len(outcomes) == 1 and outcomes[0]


def test_failed_preparation_releases_token(admissions, monkeypatch):
    monkeypatch.setattr(am, "load_conversation", lambda cid: SimpleNamespace(id=cid))
    def fail():
        assert am.is_agent_running("c")
        raise RuntimeError("preparation failed")
    monkeypatch.setattr(am.PanelSettings, "load", fail)
    with pytest.raises(RuntimeError, match="preparation failed"):
        am.run_message("c", "hi", "agent", "model", _local=True)
    assert not am.is_agent_running("c")


def test_reserved_turn_queues_instead_of_cancelling(admissions, monkeypatch):
    token = am._reserve_admission("c")
    monkeypatch.setattr(am, "load_conversation", lambda cid: SimpleNamespace(id=cid))
    assert am.run_message("c", "queued", "agent", "model", queue_if_busy=True, _local=True) == "queued"
    assert am._admissions["c"] is token
    assert am._pending_agent_messages["c"] == ["queued"]


def test_reserved_turn_is_listed_before_session_exists(admissions):
    am._reserve_admission("c")
    assert am.list_running_agents() == ["c"]


def test_cancelled_start_never_overwrites_new_session(admissions, monkeypatch):
    session = am.AgentSession()
    old = am._reserve_admission("c")
    monkeypatch.setattr(am._admission_context, "current", ("c", old), raising=False)
    session.prepare_run("old")
    am._release_admission("c", old)
    new = am._reserve_admission("c")
    am._admission_context.current = ("c", new)
    session.prepare_run("new")
    new_cancel = session._cancel
    am._admission_context.current = ("c", old)
    with pytest.raises(am._AdmissionCancelled):
        session.start(lambda: pytest.fail("stale target ran"), "old")
    assert session.run_id == "new"
    assert session._cancel is new_cancel and not new_cancel.is_set()
    assert am._admissions["c"] is new


def test_start_failure_releases_its_token(admissions, monkeypatch):
    from frontend.ui_web.live_agent_runs import get_live_run_ids
    token = am._reserve_admission("c")
    monkeypatch.setattr(am._admission_context, "current", ("c", token), raising=False)
    session = am.AgentSession()
    session.prepare_run("failed-start")
    class FailedThread:
        def __init__(self, **kwargs): pass
        def start(self): raise RuntimeError("start failed")
        def is_alive(self): return False
    monkeypatch.setattr(am.threading, "Thread", FailedThread)
    with pytest.raises(RuntimeError, match="start failed"):
        session.start(lambda: None, "failed-start")
    assert not am.is_agent_running("c")
    assert "failed-start" not in get_live_run_ids()


def test_delayed_old_worker_cannot_run_or_release_new_token(admissions, monkeypatch):
    callbacks = []
    class DelayedThread:
        def __init__(self, *, target, **kwargs): callbacks.append(target)
        def start(self): pass
        def is_alive(self): return False
    monkeypatch.setattr(am.threading, "Thread", DelayedThread)
    session = am.AgentSession()
    old = am._reserve_admission("c")
    monkeypatch.setattr(am._admission_context, "current", ("c", old), raising=False)
    session.prepare_run("old")
    session.start(lambda: pytest.fail("cancelled target ran"), "old")
    am._release_admission("c", old)
    new = am._reserve_admission("c")
    callbacks[0]()
    assert am._admissions["c"] is new
