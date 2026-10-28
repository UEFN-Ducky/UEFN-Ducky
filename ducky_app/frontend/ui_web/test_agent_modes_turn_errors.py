"""A failed turn keeps its error text, so a plan limit is not counted as a crash."""

import asyncio
import threading
from types import SimpleNamespace

from frontend.ui_web import agent_modes as am

LIMIT = "Error code: 429 - You have hit your rate limit"


def test_embedded_error_turn_ends_with_its_error_text(monkeypatch):
    from backend.agent.coding_agents import runner as coding_runner
    from frontend import usage_stats
    from frontend.ui_web import project_chats

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
    monkeypatch.setattr(am, "AgentRunner", FakeRunner)
    monkeypatch.setattr(coding_runner, "_TurnCheckpoint", FakeCkpt)
    monkeypatch.setattr(project_chats, "upsert_in_flight_assistant", lambda *a, **kw: None)
    monkeypatch.setattr(am, "_log_agent_crash", lambda *a, **kw: None)
    monkeypatch.setattr(am, "close_changeset_run", lambda *a: None)
    monkeypatch.setattr(usage_stats, "run_end", lambda rid, reason, detail="": ended.append((reason, detail)))
    session = SimpleNamespace(_cancel=threading.Event(), set_runner=lambda *a, **kw: None,
                              clear_runner=lambda *a, **kw: None)
    pushed: list[dict] = []
    reason = asyncio.run(am._run_agent_loop(
        SimpleNamespace(id="c1"), "hi", [], config=SimpleNamespace(provider="anthropic", model="m"),
        push=pushed.append, session=session, run_id="r1"))
    assert reason == "error"
    stopped = [e for e in pushed if e["type"] == "agent_stopped"]
    assert [e["detail"] for e in stopped] == [LIMIT]
    assert ended == [("error", LIMIT)]
    assert usage_stats._turn_outcome(*ended[0]) == "limit"


def test_a_limit_is_logged_but_not_counted_as_an_agent_crash(monkeypatch):
    from frontend import agent_crash_log, error_log

    logged: list[tuple] = []
    monkeypatch.setattr(agent_crash_log, "record_crash", lambda **kw: None)
    monkeypatch.setattr(error_log, "record_error",
                        lambda source, message, *, count=True: logged.append((source, count)))
    for err in (LIMIT, "Error code: 400 - messages.5: bad request"):
        am._log_agent_crash(None, provider="anthropic", model="m", error=err, elapsed_s=1.0, first_token_s=None)
    assert logged == [("agent", False), ("agent", True)]
