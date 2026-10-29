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
    from frontend import notifications
    from frontend.ui_web import context_omit, conversation_attachments, project_chats

    chats: dict[str, SimpleNamespace] = {}
    parent = {"id": ""}

    def conv(cid, model="gpt-test"):
        chats[cid] = SimpleNamespace(
            id=cid, title=cid, folder_id="", coding_agent="codex", provider="openai", model=model,
            messages=[{"role": "user", "content": "earlier"}])

    turns: list[tuple[str, str]] = []
    alerts: list[tuple] = []
    events: list[dict] = []

    def finish_turn(fresh, text, *, push, run_id, **kwargs):
        turns.append((fresh.id, text))
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
    monkeypatch.setattr(agent_modes, "_held_messages", {})
    monkeypatch.setattr(agent_modes, "_panel_push", events.append)  # never the live panel
    monkeypatch.setattr(notifications, "alert", lambda *a, **kw: alerts.append(a) or True)
    return SimpleNamespace(conv=conv, parent=parent, chats=chats, turns=turns, alerts=alerts, events=events)


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
    # Sends that wait before a turn starts: no model picked for the chat.
    for n in range(25):
        external_turns.conv(f"nomodel{n}", model="")
        assert agent_modes.run_message(f"nomodel{n}", "go", "agent", "", push=pushed.append) == agent_modes.HELD_FOR_MODEL
    assert agent_modes._run_started == {}
    assert agent_modes._quiet_runs == set()
    assert agent_modes._sessions == {}


def _wait_for(check, seconds=10.0):
    deadline = time.monotonic() + seconds
    while not check() and time.monotonic() < deadline:
        time.sleep(0.01)
    return check()


def test_a_message_to_a_chat_with_no_model_waits_and_runs_once_one_is_picked(external_turns):
    # Oct 16 2026: a ducky opened with no usable model, the message it was sent errored
    # ("No exact model selected for this chat") and the chain that sent it stopped there.
    external_turns.conv("member", model="")
    pushed: list[dict] = []
    assert agent_modes.run_message("member", "build the parser", "agent", "", push=pushed.append,
                                   parent="lead") == agent_modes.HELD_FOR_MODEL
    assert agent_modes.run_message("member", "build the parser", "agent", "", push=pushed.append)         == agent_modes.HELD_FOR_MODEL  # the same message again (a retry) is kept once
    assert agent_modes.held_for_model("member") == 1
    needed = [e for e in pushed if e["type"] == "model_needed"]
    assert needed and needed[-1]["conv_id"] == "member" and needed[-1]["held"] == 1
    assert external_turns.alerts and external_turns.alerts[-1][1] == "A message is waiting for you to pick a model."
    assert external_turns.turns == [] and agent_modes.list_running_agents() == []

    external_turns.chats["member"].model = "gpt-6-luna"  # the person picks a model
    assert agent_modes.release_held_for_model("member") == 1
    assert _wait_for(lambda: external_turns.turns == [("member", "build the parser")])
    assert agent_modes.held_for_model("member") == 0
    assert agent_modes.release_held_for_model("member") == 0
    # The run ends after its turn is recorded; a busy machine showed it to the next test.
    assert _wait_for(lambda: agent_modes.list_running_agents() == [])


def test_a_model_on_a_gateway_that_isnt_connected_says_which_and_offers_connect(external_turns, monkeypatch):
    # Oct 20 2026: the chat said "Interrupted: No API key configured", which meant nothing to
    # someone who doesn't know what an API key is, and offered no way to fix it.
    from backend.agent import model_pricing
    from backend.uefn_plugins import host

    external_turns.conv("chat", model="gpt-6-luna")
    external_turns.chats["chat"].coding_agent = "ducky"
    monkeypatch.setattr(model_pricing, "resolve_provider_for_model", lambda model, provider: "openai")
    monkeypatch.setattr(host, "resolve_gateway_credential", lambda provider: None)
    monkeypatch.setattr(host, "get_contributions", lambda: {"llm_providers": [{"id": "openai", "label": "OpenAI"}]})
    monkeypatch.setattr(agent_modes, "save_conversation", lambda c, **kw: None)
    pushed: list[dict] = []
    assert agent_modes.run_message("chat", "hey", "agent", "", push=pushed.append) == ""
    assert {"type": "provider_not_connected", "conv_id": "chat", "provider": "openai",
            "label": "OpenAI", "model": "gpt-6-luna"} in pushed
    errors = [e["text"] for e in pushed if e["type"] == "error"]
    assert errors == ["OpenAI isn't connected yet, so gpt-6-luna can't answer. "
                      "Connect OpenAI or pick another model below."]
    assert external_turns.turns == [] and agent_modes.list_running_agents() == []


def test_an_agent_waiting_on_a_reply_hears_the_chat_needs_a_model(external_turns):
    external_turns.conv("member", model="")
    out = agent_modes.run_message_and_wait("member", "build the parser", timeout_sec=5)
    assert out["status"] == "waiting_for_model"
    assert "your message runs then" in out["error"]
    assert agent_modes.held_for_model("member") == 1


def test_a_ducky_chat_with_no_model_takes_the_fallback_model(monkeypatch):
    import frontend.favorite_models as fm

    fallback = fm.ResolveOk(coding_agent="ducky", model="hosted-ai", provider="hosted",
                            selection=fm.FavoriteSelection(backend="hosted", model_id="hosted-ai"))
    monkeypatch.setattr(fm, "chat_model_choice", lambda settings, keep_agent=False: (fallback, ""))
    monkeypatch.setattr(agent_modes, "save_conversation", lambda c, **kw: None)
    monkeypatch.setattr(agent_modes, "notify_chats_changed", lambda *a, **kw: None)
    conv = SimpleNamespace(id="c1", title="Builder", folder_id="", coding_agent="ducky", model="", provider="")
    pushed: list[dict] = []
    assert agent_modes._take_chat_model(conv, SimpleNamespace(), pushed.append) == ("hosted-ai", "ducky", "")
    assert (conv.coding_agent, conv.model, conv.provider) == ("ducky", "hosted-ai", "hosted")
    assert pushed == [{"type": "model_adopted", "conv_id": "c1", "model": "hosted-ai",
                       "coding_agent": "ducky", "provider": "hosted"}]
    monkeypatch.setattr(fm, "chat_model_choice", lambda settings, keep_agent=False: (None, "Pick a model."))
    conv.model = ""
    assert agent_modes._take_chat_model(conv, SimpleNamespace(), pushed.append) == ("", "ducky", "Pick a model.")
