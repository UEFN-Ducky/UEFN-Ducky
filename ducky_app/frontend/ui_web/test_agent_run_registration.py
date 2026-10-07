"""A worker that exits immediately must not leave permissions locked."""
import pytest
from frontend.ui_web import agent_modes
from frontend.ui_web.live_agent_runs import discard_live_run_id, get_live_run_ids


def test_registration_precedes_worker_start_even_if_worker_exits_immediately(monkeypatch):
    class ImmediateThread:
        def __init__(self, target, *, daemon, name):
            self.target = target
            self.name = name

        def start(self):
            self.target()

    monkeypatch.setattr(agent_modes.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(agent_modes, "_dbg_thread_state", lambda *args, **kwargs: None)
    session = agent_modes.AgentSession()
    session.run_id = "instant-run"

    def work():
        assert "instant-run" in get_live_run_ids()
        discard_live_run_id("instant-run")

    session.start(work, "instant-run")
    assert "instant-run" not in get_live_run_ids()


def test_failed_worker_start_does_not_leave_a_live_run(monkeypatch):
    class FailedThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise RuntimeError("could not start")

    monkeypatch.setattr(agent_modes.threading, "Thread", FailedThread)
    session = agent_modes.AgentSession()
    session.run_id = "failed-start"
    with pytest.raises(RuntimeError, match="could not start"):
        session.start(lambda: None, "failed-start")
    assert "failed-start" not in get_live_run_ids()
