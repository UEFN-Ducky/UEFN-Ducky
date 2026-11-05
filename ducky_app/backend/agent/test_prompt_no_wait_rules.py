"""Agents never wait on each other: the rule text says so and no turn-taking text is left."""

from __future__ import annotations

import pytest

from backend.agent.hard_rules import AGENT_HARD_RULES, CORE_RULES, hard_rules_for
from backend.agent.prompt import _rules_body

NEW_RULES = (
    "Never wait on another agent",
    "`sandbox_worktree` of `main`",
    "No branches: push to main",
    "`git pull --rebase origin main`",
    "`git push origin HEAD:main`",
    "run no test suites, builds or live panels",
    "final test phase",
)

OLD_PHRASES = (
    "one heavy run at a time",
    "nothing else runs tests",
    "nothing else should run tests",
    "take turns",
    "turn-taking",
    "test windows",
    "one member runs full plugin tests",
    "Waiting for",
)


@pytest.mark.parametrize("rules", [CORE_RULES, AGENT_HARD_RULES, hard_rules_for("")])
def test_hard_rules_have_the_no_wait_rules(rules: str) -> None:
    for phrase in NEW_RULES:
        assert phrase in rules, phrase


def test_folder_projects_get_the_no_wait_rules_too() -> None:
    # The team rules are core rules: a folder project (no island rules) keeps them.
    assert "Never wait on another agent" in CORE_RULES
    assert "Test last (HARD)" in CORE_RULES


def test_chat_prompt_team_rules_never_wait() -> None:
    body = _rules_body(18765)
    assert "Never wait on another agent: each member works in its own `sandbox_worktree` of main." in body
    assert "No branches: push to main" in body
    assert "Build points run no test suites, builds or live panels" in body
    assert "final test phase" in body


def _bootstrap() -> str:
    from backend.agent.coding_agents.mcp_inject import bootstrap_system_prompt

    return bootstrap_system_prompt(project_root="", conv_id="chat-under-test")


def test_coding_agent_bootstrap_never_waits() -> None:
    # Claude Code / Codex members read the bootstrap prompt, not prompt.py.
    text = _bootstrap()
    assert "Never wait on another agent: each member works in its own `sandbox_worktree` of main." in text
    assert "No branches: push to main" in text
    assert "`git push origin HEAD:main`" in text
    assert "Build points run no test suites, builds or live panels" in text
    assert "final test phase" in text


def test_coding_agent_bootstrap_has_no_turn_taking_text() -> None:
    lowered = _bootstrap().lower()
    # "waiting for the UEFN listener" (desktop plugins) is about the editor, not agents.
    for phrase in (p for p in OLD_PHRASES if p != "Waiting for"):
        assert phrase.lower() not in lowered, phrase


@pytest.mark.parametrize("local_slim", [False, True])
def test_no_turn_taking_text_left(local_slim: bool) -> None:
    text = _rules_body(18765, local_slim=local_slim) + AGENT_HARD_RULES
    lowered = text.lower()
    for phrase in OLD_PHRASES:
        assert phrase.lower() not in lowered, phrase
