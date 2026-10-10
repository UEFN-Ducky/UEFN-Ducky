"""A worker that exits immediately must not leave permissions locked."""
import threading
import time
from types import SimpleNamespace

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


def _join(threads):
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()


def test_finished_runs_leave_no_session_behind(monkeypatch):
    monkeypatch.setattr(agent_modes, "_sessions", {})
    threads = []
    for n in range(50):
        session = agent_modes._session(f"chat{n}")
        session.prepare_run(f"run{n}")
        session.start(lambda: None, f"run{n}")
        threads.append(session._thread)
    _join(threads)
    assert agent_modes._sessions == {}
    assert not any(agent_modes.is_agent_running(f"chat{n}") for n in range(50))


def test_a_follow_up_prepared_before_the_old_turn_ends_keeps_its_session(monkeypatch):
    monkeypatch.setattr(agent_modes, "_sessions", {})
    monkeypatch.setattr(agent_modes, "SESSION_JOIN_TIMEOUT", 0.01)
    release = threading.Event()
    session = agent_modes._session("chat")
    session.prepare_run("old")
    session.start(lambda: release.wait(5), "old")
    old = session._thread
    session.prepare_run("follow-up")  # the old turn has not finished yet
    release.set()
    _join([old])
    assert agent_modes._sessions == {"chat": session}


@pytest.fixture
def external_turns(monkeypatch, tmp_path):
    """run_message down the coding-agent path with everything outside it stubbed."""
    from backend.agent import a2a_broker, attachments
    from backend.agent.coding_agents import base, runner
    from frontend.ui_web import context_omit, conversation_attachments, project_chats

    chats: dict[str, SimpleNamespace] = {}
    parent = {"id": ""}

    def conv(cid, model="gpt-test"):
        chats[cid] = SimpleNamespace(
            id=cid, title=cid, folder_id="", coding_agent="codex", provider="openai", model=model,
            messages=[{"role": "user", "content": "earlier"}])

    def finish_turn(fresh, text, *, push, run_id, **kwargs):
        push({"type": "agent_stopped", "reason": "done", "conv_id": fresh.id, "run_id": run_id})

    monkeypatch.setattr(agent_modes, "_sessions", {})
    monkeypatch.setattr(agent_modes, "_run_started", {})
    monkeypatch.setattr(agent_modes, "_quiet_runs", set())
    monkeypatch.setattr(agent_modes, "_linked_parents", {})
    monkeypatch.setattr(agent_modes, "_in_bridge_process", lambda: False)
    monkeypatch.setattr(agent_modes, "load_conversation", lambda cid, **kw: chats.get(cid))
    monkeypatch.setattr(agent_modes, "append_message", lambda c, msg: c.messages.append(msg))
    monkeypatch.setattr(agent_modes.PanelSettings, "load", staticmethod(lambda: SimpleNamespace(
        uefn_project_root="", agent_provider="", prompt_dedupe_exact_blocks=False)))
    monkeypatch.setattr(agent_modes, "apply_workspace_env", lambda root: None)
    monkeypatch.setattr(agent_modes, "parse_attachment_dicts", lambda *a, **kw: [])
    monkeypatch.setattr(agent_modes, "_backfill_video_frames", lambda *a, **kw: None)
    monkeypatch.setattr(agent_modes, "get_active_conv_id", lambda: parent["id"])
    monkeypatch.setattr(agent_modes, "notify_chats_changed", lambda *a, **kw: None)
    monkeypatch.setattr(agent_modes, "close_changeset_run", lambda run_id, reason: None)
    monkeypatch.setattr(attachments, "prepare_outgoing_user_message", lambda text, *a, **kw: (text, None))
    monkeypatch.setattr(conversation_attachments, "persist_message_attachments", lambda *a, **kw: [])
    monkeypatch.setattr(project_chats, "get_conversations_dir", lambda root=None: tmp_path)
    monkeypatch.setattr(context_omit, "context_omit_set", lambda c: set())
    monkeypatch.setattr(base, "normalize_coding_agent", lambda value: value or "ducky")
    monkeypatch.setattr(runner, "run_coding_agent_message", finish_turn)
    monkeypatch.setattr(a2a_broker, "on_agent_stopped", lambda *a, **kw: None)
    return SimpleNamespace(conv=conv, parent=parent)


def test_external_turns_and_refused_sends_leave_no_bookkeeping(external_turns):
    pushed: list[dict] = []
    for n in range(25):
        external_turns.conv(f"ext{n}")
        external_turns.parent["id"] = "coordinator" if n % 2 else ""  # sub-agent runs are quiet
        assert agent_modes.run_message(f"ext{n}", "go", "agent", "", push=pushed.append)
    deadline = time.monotonic() + 10
    while agent_modes.list_running_agents() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert agent_modes.list_running_agents() == []
    assert sum(e.get("type") == "agent_stopped" for e in pushed) == 25
    # Sends refused before a turn starts: no model picked for the chat.
    for n in range(25):
        external_turns.conv(f"nomodel{n}", model="")
        assert agent_modes.run_message(f"nomodel{n}", "go", "agent", "", push=pushed.append) == ""
    assert agent_modes._run_started == {}
    assert agent_modes._quiet_runs == set()
    assert agent_modes._sessions == {}
