"""agent_turns.errors: what a crashed turn records, leaves in the chat and does next."""

from __future__ import annotations

from types import SimpleNamespace

from frontend.ui_web.agent_turns import errors

LIMIT = "Error code: 429 - You have hit your rate limit"


def test_a_limit_is_logged_but_not_counted_as_an_agent_crash(monkeypatch):
    from frontend import agent_crash_log, error_log

    logged: list[tuple] = []
    monkeypatch.setattr(agent_crash_log, "record_crash", lambda **kw: None)
    monkeypatch.setattr(error_log, "record_error",
                        lambda source, message, *, count=True: logged.append((source, count)))
    for err in (LIMIT, "Error code: 400 - messages.5: bad request"):
        errors._log_agent_crash(None, provider="anthropic", model="m", error=err, elapsed_s=1.0, first_token_s=None)
    assert logged == [("agent", False), ("agent", True)]


def test_the_crash_row_says_what_ran_and_what_happens_next():
    row = errors._crash_row({"role": "assistant", "content": "half"}, "boom", tools_run=2,
                            written={"b.py", "a.py"}, auto_resume=True)
    assert row["incomplete"] and row["error"] == "boom"
    assert row["content"] == "half\n\n**Interrupted** (boom) after 2 tool calls; files written: `a.py`, `b.py`. Resuming in 60 s."
    quiet = errors._crash_row(None, "boom", tools_run=0, written=set(), auto_resume=False)
    assert quiet["content"] == "" and quiet["role"] == "assistant"


def test_one_auto_resume_per_chat_in_ten_minutes(monkeypatch):
    timers: list[float] = []

    class T:
        def __init__(self, delay, fn):
            timers.append(delay)
        daemon = False
        def start(self): pass

    monkeypatch.setattr(errors.threading, "Timer", T)
    monkeypatch.setattr(errors, "_auto_resumed_at", {})
    assert errors._schedule_auto_resume("c", "503", "agent")
    assert not errors._schedule_auto_resume("c", "503", "agent")
    assert timers == [errors.AUTO_RESUME_DELAY_S]


def test_no_claude_code_means_the_turn_stops_with_a_reason(monkeypatch):
    from backend.agent.coding_agents import base

    monkeypatch.setattr(base, "get_adapter", lambda _id: None)
    monkeypatch.setattr(errors.PanelSettings, "load", staticmethod(lambda: SimpleNamespace()))
    seen: list[dict] = []
    assert not errors._continue_on_claude_code("c", "agent", seen.append)
    assert seen[0]["type"] == "error" and "Claude Code" in seen[0]["text"]
