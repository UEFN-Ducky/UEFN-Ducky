"""usage_events.kind_of: an error message read as one ERROR_KINDS word."""

from __future__ import annotations

import pytest

from frontend import usage_events as ev


@pytest.mark.parametrize(
    "message, kind",
    [
        # Turns that failed before any tool call.
        ("Ducky's tools didn't connect", "offline"),
        ("Ducky’s tools didn’t connect", "offline"),
        ("claude_code finished with no captured reply.\n\nDucky's tools didn't connect", "offline"),
        ("MCP server failed to start", "offline"),
        ("Could not connect to Ducky", "offline"),
        ("Claude Code exited unsuccessfully (code 1)", "crash"),
        ("codex process exited with code 3221225477", "crash"),
        # The rules that were there before keep their words.
        ("timed out waiting for listener idle", "busy"),
        ("503 overloaded", "rate_limit"),
        ("Session unavailable", "session_dead"),
        ("request timed out", "timeout"),
        ("Traceback (most recent call last)", "crash"),
        ("prompt is too long: 210000 tokens", "too_long"),
        ("claude_code finished with no captured reply.", "other"),
        ("", "other"),
    ],
)
def test_kind_of(message: str, kind: str) -> None:
    assert ev.kind_of(message) == kind


def test_every_kind_rule_names_a_store_word() -> None:
    assert {kind for kind, _ in ev._KIND_RULES} <= set(ev.ERROR_KINDS)
