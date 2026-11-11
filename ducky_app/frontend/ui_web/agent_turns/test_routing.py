"""agent_turns.routing: run_message with the agent host on (coding turns go to the host; no
host, the app runs them), and the mode suffixes."""

from frontend.ui_web.agent_turns import routing, state
from frontend.ui_web.test_agent_run_registration import _wait_for, external_turns  # noqa: F401 - fixture


def test_each_mode_has_its_own_suffix():
    assert "[Mode: Ask]" in routing._ASK_SUFFIX
    assert "[Mode: Plan]" in routing._PLAN_SUFFIX and "ducky_create_plan" in routing._PLAN_SUFFIX
    assert "[Mode: Agent]" in routing._AGENT_SUFFIX


def test_with_the_host_on_a_coding_turn_runs_in_the_host(external_turns, monkeypatch):  # noqa: F811
    from frontend.agent_host import turns as host_turns

    monkeypatch.setenv("DUCKY_AGENT_HOST", "1")
    handed: list[tuple] = []

    def in_host(conv_id, run_id, text, *, model, mode, push, cancel, on_started=None):
        handed.append((conv_id, text, model, mode))
        push({"type": "agent_stopped", "reason": "done", "conv_id": conv_id, "run_id": run_id})
        return ""

    monkeypatch.setattr(host_turns, "run_in_host", in_host)
    pushed: list[dict] = []
    external_turns.conv("hosted")
    assert routing.run_message("hosted", "go", "agent", "", push=pushed.append)
    assert _wait_for(lambda: state.list_running_agents() == [])
    assert handed == [("hosted", "go", "gpt-test", "agent")]
    assert external_turns.turns == []  # the app never started the CLI itself
    assert [e["reason"] for e in pushed if e["type"] == "agent_stopped"] == ["done"]


def test_a_host_turn_counts_as_running_in_the_app_until_it_ends(external_turns, monkeypatch):  # noqa: F811
    # The terminal reaper keeps a chat's terminal while list_running_agents() names the chat:
    # a turn in the agent host must be named while it runs and dropped when it ends.
    import threading

    from frontend.agent_host import turns as host_turns

    monkeypatch.setenv("DUCKY_AGENT_HOST", "1")
    started, end = threading.Event(), threading.Event()

    def in_host(conv_id, run_id, text, *, model, mode, push, cancel, on_started=None):
        started.set()
        end.wait(10)
        return ""

    monkeypatch.setattr(host_turns, "run_in_host", in_host)
    external_turns.conv("hosted")
    assert routing.run_message("hosted", "go", "agent", "", push=lambda e: None)
    assert started.wait(10)
    assert state.list_running_agents() == ["hosted"]
    end.set()  # the host's turn_ended frame
    assert _wait_for(lambda: state.list_running_agents() == [])


def test_a_turn_end_closes_its_chats_terminals_unless_a_question_waits(external_turns, monkeypatch):  # noqa: F811
    from frontend.agent_host import turns as host_turns
    from frontend.ui_web import ui_rpc
    from frontend.ui_web.terminal import manager as terminal_manager

    monkeypatch.setenv("DUCKY_AGENT_HOST", "1")
    monkeypatch.setattr(host_turns, "run_in_host", lambda *a, **kw: "")  # the host turn ended
    closed: list[str] = []

    class _Terminals:
        def close_for_turn_end(self, conv_id):
            closed.append(conv_id)

    monkeypatch.setattr(terminal_manager, "_manager", _Terminals())
    asking = {"q": None}
    monkeypatch.setattr(ui_rpc, "pending_question", lambda cid: asking["q"])
    external_turns.conv("hosted")
    assert routing.run_message("hosted", "go", "agent", "", push=lambda e: None)
    assert _wait_for(lambda: closed == ["hosted"])
    asking["q"] = {"request_id": "r1", "text": "Which one?", "since": 0}  # paused on a question card
    assert routing.run_message("hosted", "again", "agent", "", push=lambda e: None)
    assert _wait_for(lambda: state.list_running_agents() == [])
    assert closed == ["hosted"]


def test_no_host_falls_back_and_a_host_error_stops_the_turn(external_turns, monkeypatch):  # noqa: F811
    from frontend.agent_host import turns as host_turns

    monkeypatch.setenv("DUCKY_AGENT_HOST", "1")
    outcomes = iter([None, "The agent host stopped (eof)."])
    monkeypatch.setattr(host_turns, "run_in_host", lambda *a, **kw: next(outcomes))
    pushed: list[dict] = []
    external_turns.conv("a")
    assert routing.run_message("a", "first", "agent", "", push=pushed.append)
    assert _wait_for(lambda: external_turns.turns == [("a", "first")])  # ran in the app
    assert _wait_for(lambda: state.list_running_agents() == [])
    pushed.clear()
    assert routing.run_message("a", "second", "agent", "", push=pushed.append)
    assert _wait_for(lambda: state.list_running_agents() == [])
    assert any(e["type"] == "error" and "agent host stopped" in e["text"] for e in pushed)
    stopped = [e for e in pushed if e["type"] == "agent_stopped"]
    assert [(e["reason"], e["detail"]) for e in stopped] == [("error", "The agent host stopped (eof).")]


def test_a_dead_hosts_turn_is_interrupted_with_its_checkpoint_kept(external_turns, monkeypatch):  # noqa: F811
    from backend.agent.coding_agents import runner
    from frontend.agent_host import turns as host_turns

    monkeypatch.setenv("DUCKY_AGENT_HOST", "1")

    def in_host(conv_id, run_id, *a, **kw):
        # What the host saved before it died: a partial reply with one tool call.
        external_turns.chats[conv_id].messages.append(
            {"role": "assistant", "run_id": run_id, "text": "half", "blocks": [{"type": "text", "text": "half"}]})
        return host_turns.INTERRUPTED

    monkeypatch.setattr(host_turns, "run_in_host", in_host)
    kept: list[dict] = []
    monkeypatch.setattr(runner, "checkpoint_coding_turn", lambda conv, **kw: kept.append(kw))
    external_turns.conv("x")
    external_turns.chats["x"].session_id = "sess-1"
    pushed: list[dict] = []
    assert routing.run_message("x", "go", "agent", "", push=pushed.append)
    assert _wait_for(lambda: state.list_running_agents() == [])
    stopped = [e for e in pushed if e["type"] == "agent_stopped"]
    assert [(e["reason"], e["detail"]) for e in stopped] == [("interrupted", host_turns.INTERRUPTED)]
    assert any(e["type"] == "error" and e["text"] == host_turns.INTERRUPTED for e in pushed)
    assert kept and kept[0]["reply"] == "half" and kept[0]["blocks"] == [{"type": "text", "text": "half"}]
    assert kept[0]["error"] == host_turns.INTERRUPTED
    assert external_turns.chats["x"].session_id == "sess-1"  # kept for --resume


def test_a_turn_in_the_host_does_not_keep_the_chat_in_the_app(external_turns, monkeypatch):  # noqa: F811
    """The app held its own copy of the chat until the turn ended: every message of it,
    130 MB for one long chat that ran for hours (Nov 11 2026)."""
    import gc
    import threading
    import weakref
    from types import SimpleNamespace

    from frontend.agent_host import turns as host_turns
    from frontend.ui_web.agent_turns.testing import patch_turns

    monkeypatch.setenv("DUCKY_AGENT_HOST", "1")
    took, end = threading.Event(), threading.Event()

    def in_host(conv_id, run_id, text, *, model, mode, push, cancel, on_started=None):
        on_started()  # the host has the turn
        took.set()
        end.wait(10)
        return ""

    monkeypatch.setattr(host_turns, "run_in_host", in_host)
    made: list[weakref.ref] = []

    class Chat(SimpleNamespace):
        pass

    def load(cid, **kw):
        chat = Chat(id=cid, title=cid, folder_id="", coding_agent="codex", provider="openai", model="gpt-test",
                    messages=[{"role": "user", "content": "earlier"}])
        made.append(weakref.ref(chat))
        return chat

    patch_turns(monkeypatch, "load_conversation", load)
    assert routing.run_message("hosted", "go", "agent", "", push=lambda e: None)
    assert took.wait(10)
    gc.collect()
    try:
        assert made and [ref for ref in made if ref() is not None] == [], "the app still holds the chat"
    finally:
        end.set()
    assert _wait_for(lambda: state.list_running_agents() == [])
