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


def test_launcher_start_is_kept_and_bucketed() -> None:
    assert ev.validate("launcher.start", {"mode": "native", "fallback": "none", "painted_ms": 640, "window_ms": 95}) == {
        "mode": "native", "fallback": "none", "painted_ms": "1100ms", "window_ms": "100ms"}
    assert ev.validate("launcher.start", {"mode": "python", "fallback": "no_hello"}) == {
        "mode": "python", "fallback": "no_hello"}
    assert ev.validate("launcher.start", {"painted_ms": 1250})["painted_ms"] == "1300ms"
    # Free text and unknown modes never pass.
    assert ev.validate("launcher.start", {"mode": "rust", "fallback": "C:/Users/x"}) == {}


def test_launcher_fallbacks_match_native_switch() -> None:
    from frontend import native_switch

    assert set(native_switch.FALLBACK_REASONS) | {"none"} == set(ev.LAUNCHER_FALLBACKS)


def test_track_keeps_launcher_start(monkeypatch: pytest.MonkeyPatch) -> None:
    from frontend import usage_stats as us

    rows: list = []
    monkeypatch.setattr(us, "enabled", lambda: True)
    monkeypatch.setattr(us, "_enqueue", lambda row: rows.append(row) or len(rows))
    assert us.track("launcher.start", mode="native", fallback="none", painted_ms=900, window_ms=80) is True
    assert rows[0]["name"] == "launcher.start"
    assert rows[0]["props"] == {"mode": "native", "fallback": "none", "painted_ms": "1100ms", "window_ms": "100ms"}
