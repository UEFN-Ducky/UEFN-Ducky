"""A team chat whose session got too big starts its next turn fresh, with a handoff."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.agent.coding_agents import session_refresh as sr


def _conv(tokens: int, session: str = "thread-1") -> SimpleNamespace:
    return SimpleNamespace(
        id="lead",
        upstream_session_id=session,
        coding_agent_stats={"context_tokens": tokens},
        messages=[
            {"role": "user", "text": "build it"},
            {"role": "assistant", "text": "Integrated the timeline batch; 191 checks passed.", "ts": 1791757863.3},
            {"role": "assistant", "text": "Only native-MOV fails; Render Jobs is fixing it.", "ts": 1791758454.6},
        ],
    )


@pytest.mark.parametrize("tokens,expires,session,want", [
    (250_000, 900.0, "thread-1", True),    # big and cold: compact first
    (250_000, 1100.0, "thread-1", False),  # cache still warm
    (50_000, 900.0, "thread-1", False),    # small: a cold resume is cheap
    (250_000, 0.0, "thread-1", False),     # no cache info
    (250_000, 900.0, "", False),           # nothing to resume
])
def test_cold_big_sessions_compact_first(tokens, expires, session, want):
    conv = _conv(tokens, session)
    conv.coding_agent_stats["cache_expires_at"] = expires
    assert sr.needs_cold_compact(conv, now=1000.0) is want


def test_team_chats_get_the_hour_long_cache(monkeypatch):
    from backend.agent.coding_agents.runner import _team_cache_env

    monkeypatch.delenv("CLAUDE_CODE_PROMPT_CACHE_TTL", raising=False)
    monkeypatch.setattr(sr, "_team_chat", lambda conv_id: conv_id == "member")
    assert _team_cache_env("claude_code", "member") == {"CLAUDE_CODE_PROMPT_CACHE_TTL": "1h"}
    assert _team_cache_env("claude_code", "solo") == {}
    assert _team_cache_env("codex", "member") == {}
    monkeypatch.setenv("CLAUDE_CODE_PROMPT_CACHE_TTL", "5m")
    assert _team_cache_env("claude_code", "member") == {}


@pytest.fixture
def in_team(monkeypatch):
    monkeypatch.setattr(sr, "_team_chat", lambda conv_id: True)
    monkeypatch.setattr(sr, "_plan_outline", lambda conv_id: ["15 of 19 steps done. Open steps:", "- 4.3 in_progress: Check all paths"])


def test_a_big_team_session_starts_fresh_with_its_own_last_replies_and_plan(in_team) -> None:
    conv = _conv(5_300_000)
    handoff = sr.fresh_session_handoff(conv)
    assert handoff.startswith("## Fresh session")
    assert "5.3M tokens" in handoff
    assert "Only native-MOV fails" in handoff and "191 checks passed" in handoff
    assert handoff.index("191 checks") < handoff.index("native-MOV")  # newest last
    assert "4.3 in_progress" in handoff
    sr.start_fresh(conv)
    assert conv.upstream_session_id == "" and conv.coding_agent_stats is None
    assert sr.fresh_session_handoff(conv) == ""  # the new session is not refreshed again


def test_small_sessions_new_sessions_and_solo_chats_keep_going(in_team, monkeypatch) -> None:
    assert sr.fresh_session_handoff(_conv(400_000)) == ""
    assert sr.fresh_session_handoff(_conv(5_300_000, session="")) == ""
    monkeypatch.setattr(sr, "_team_chat", lambda conv_id: False)
    assert sr.fresh_session_handoff(_conv(5_300_000)) == ""


def test_an_agent_is_told_when_its_chat_allows_everything(monkeypatch) -> None:
    # Oct 11 2026: the team lead asked the person to approve a command twice after they had
    # turned on Allow everything for the whole team.
    from backend.agent.coding_agents import runner

    monkeypatch.setattr("backend.tools.panel.permission_prompt.allows_everything", lambda cid: cid == "lead")
    assert "Never ask the person to approve a command" in runner.permission_note("lead")
    assert runner.permission_note("builder") == ""
