"""agent_turns.run: the embedded turn loop and its helpers."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

from frontend.ui_web.agent_turns import run, state

LIMIT = "Error code: 429 - You have hit your rate limit"


def test_embedded_error_turn_ends_with_its_error_text(monkeypatch):
    """A failed turn keeps its error text, so a plan limit is not counted as a crash."""
    from backend.agent.coding_agents import runner as coding_runner
    from frontend import usage_stats
    from frontend.ui_web.chats import saves as chat_saves

    class FakeRunner:
        def __init__(self, config): pass
        def set_approval_callback(self, cb): pass
        def cancel(self): pass

        async def run_turn(self, *a, **kw):
            yield SimpleNamespace(kind="error", text=LIMIT, partial_message=None)

    class FakeCkpt:
        def __init__(self, *a): pass
        def wrap(self, push): return push
        def seed(self): pass
        def flush(self, **kw): pass

    ended: list[tuple] = []
    monkeypatch.setattr(run, "AgentRunner", FakeRunner)
    monkeypatch.setattr(coding_runner, "_TurnCheckpoint", FakeCkpt)
    monkeypatch.setattr(chat_saves, "upsert_in_flight_assistant", lambda *a, **kw: None)
    monkeypatch.setattr(run, "_log_agent_crash", lambda *a, **kw: None)
    monkeypatch.setattr(state, "close_changeset_run", lambda *a: None)
    monkeypatch.setattr(usage_stats, "run_end", lambda rid, reason, detail="": ended.append((reason, detail)))
    session = SimpleNamespace(_cancel=threading.Event(), set_runner=lambda *a, **kw: None,
                              clear_runner=lambda *a, **kw: None)
    pushed: list[dict] = []
    reason = asyncio.run(run._run_agent_loop(
        SimpleNamespace(id="c1"), "hi", [], config=SimpleNamespace(provider="anthropic", model="m"),
        push=pushed.append, session=session, run_id="r1"))
    assert reason == "error"
    stopped = [e for e in pushed if e["type"] == "agent_stopped"]
    assert [e["detail"] for e in stopped] == [LIMIT]
    assert ended == [("error", LIMIT)]
    assert usage_stats._turn_outcome(*ended[0]) == "limit"


def test_tool_lines_and_results_stay_short():
    assert run._tool_line("t", {"a": 1}) == 't({"a": 1})'
    assert run._tool_line("t", {}, "success", 5) == "t({}) · success · 5ms"
    assert run._tool_line("t", {"a": "x" * 200}).endswith("...)")
    assert run._cap_ui_result("abc", limit=2).startswith("ab\n…")
    assert run._tool_result_text({"error": "bad"}) == "bad" and run._tool_result_text(None) == ""


def test_resume_drops_an_empty_crash_row_and_finds_the_last_user_message():
    conv = SimpleNamespace(messages=[
        {"role": "user", "text": "go", "content": "", "attachments": [{"name": "a"}]},
        {"role": "assistant", "content": "", "incomplete": True, "error": "boom"},
    ])
    assert run.clear_last_interrupt(conv) and len(conv.messages) == 1
    assert run.last_user_payload(conv) == ("go", "go", [{"name": "a"}])
    assert not run.clear_last_interrupt(conv)
    kept = SimpleNamespace(messages=[{"role": "assistant", "content": "half", "incomplete": True, "error": "x"}])
    assert run.clear_last_interrupt(kept) and kept.messages == [{"role": "assistant", "content": "half"}]
