"""run_message with the agent host on: coding turns go to the host; no host, the app runs them."""

from frontend.ui_web import agent_modes
from frontend.ui_web.test_agent_run_registration import _wait_for, external_turns  # noqa: F401 - fixture


def test_with_the_host_on_a_coding_turn_runs_in_the_host(external_turns, monkeypatch):  # noqa: F811
    from frontend.agent_host import turns as host_turns

    monkeypatch.setenv("DUCKY_AGENT_HOST", "1")
    handed: list[tuple] = []

    def in_host(conv_id, run_id, text, *, model, mode, push, cancel):
        handed.append((conv_id, text, model, mode))
        push({"type": "agent_stopped", "reason": "done", "conv_id": conv_id, "run_id": run_id})
        return ""

    monkeypatch.setattr(host_turns, "run_in_host", in_host)
    pushed: list[dict] = []
    external_turns.conv("hosted")
    assert agent_modes.run_message("hosted", "go", "agent", "", push=pushed.append)
    assert _wait_for(lambda: agent_modes.list_running_agents() == [])
    assert handed == [("hosted", "go", "gpt-test", "agent")]
    assert external_turns.turns == []  # the app never started the CLI itself
    assert [e["reason"] for e in pushed if e["type"] == "agent_stopped"] == ["done"]


def test_no_host_falls_back_and_a_host_error_stops_the_turn(external_turns, monkeypatch):  # noqa: F811
    from frontend.agent_host import turns as host_turns

    monkeypatch.setenv("DUCKY_AGENT_HOST", "1")
    outcomes = iter([None, "The agent host stopped (eof)."])
    monkeypatch.setattr(host_turns, "run_in_host", lambda *a, **kw: next(outcomes))
    pushed: list[dict] = []
    external_turns.conv("a")
    assert agent_modes.run_message("a", "first", "agent", "", push=pushed.append)
    assert _wait_for(lambda: external_turns.turns == [("a", "first")])  # ran in the app
    assert _wait_for(lambda: agent_modes.list_running_agents() == [])
    pushed.clear()
    assert agent_modes.run_message("a", "second", "agent", "", push=pushed.append)
    assert _wait_for(lambda: agent_modes.list_running_agents() == [])
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
    assert agent_modes.run_message("x", "go", "agent", "", push=pushed.append)
    assert _wait_for(lambda: agent_modes.list_running_agents() == [])
    stopped = [e for e in pushed if e["type"] == "agent_stopped"]
    assert [(e["reason"], e["detail"]) for e in stopped] == [("interrupted", host_turns.INTERRUPTED)]
    assert any(e["type"] == "error" and e["text"] == host_turns.INTERRUPTED for e in pushed)
    assert kept and kept[0]["reply"] == "half" and kept[0]["blocks"] == [{"type": "text", "text": "half"}]
    assert kept[0]["error"] == host_turns.INTERRUPTED
    assert external_turns.chats["x"].session_id == "sess-1"  # kept for --resume
