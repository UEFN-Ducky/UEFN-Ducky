"""Shared helpers for the streaming-CLI adapters (claude_code.py, codex.py).

Both adapters shell out to a CLI via ``run_streaming_process``, parse a JSON
event stream into text/tool blocks, then translate the finished
``ProcResult`` into a ``CodingAgentLaunchResult``. This module holds the bits
of that pipeline that were byte-for-byte (or near enough) duplicated.
"""

from __future__ import annotations

from typing import Any

from backend.agent.coding_agents.base import CodingAgentLaunchResult
from backend.agent.coding_agents.proc_exec import ProcResult

# Substrings checked against a lowercased error message to classify a failed
# turn as "needs_login" rather than a plain error. Superset of both CLIs'
# marker lists (e.g. "codex login" and "not logged in" already contain
# "login" / "not logged"), so sharing this one set narrows nothing.
LOGIN_MARKERS = ("sign in", "log in", "login", "oauth", "not logged", "authenticate", "/login")


def truncate_tool_result(text: str, max_chars: int = 4000) -> str:
    """Trim a tool-result string for the panel UI, flagging truncation."""
    text = (text or "").strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "…(truncated)"
    return text


def finalize_cli_turn(
    *,
    proc: ProcResult,
    reply: str,
    streamed: bool,
    blocks: list[dict[str, Any]],
    session_id: str,
    new_session: str,
    usage: dict[str, Any],
    agent_label: str,
    timeout_s: float,
    error_text: str,
    stale_session_markers: tuple[str, ...] = (),
) -> CodingAgentLaunchResult:
    """Turn a finished ``ProcResult`` into a ``CodingAgentLaunchResult``.

    Shared cancelled → timed-out → error (login-marker + stale-session
    detection) → success branching that both CLI adapters need after their
    ``run_streaming_process`` call.
    """
    if proc.cancelled:
        return CodingAgentLaunchResult(
            ok=False,
            upstream_session_id=new_session,
            reply_text=reply,
            streamed=streamed,
            error="Cancelled",
            status="cancelled",
            blocks=blocks,
        )
    if proc.timed_out:
        return CodingAgentLaunchResult(
            ok=False,
            upstream_session_id=new_session,
            reply_text=reply,
            streamed=streamed,
            error=f"{agent_label} timed out after {int(timeout_s)}s",
            status="timeout",
            blocks=blocks,
        )
    if error_text or (proc.returncode != 0 and not reply):
        error = error_text or proc.stderr_tail.strip() or proc.raw_tail or f"{agent_label} failed"
        low = error.lower()
        status = "needs_login" if any(m in low for m in LOGIN_MARKERS) else "error"
        if session_id and any(m in low for m in stale_session_markers):
            # A stale resume id makes the CLI exit with a "not found"-style
            # error; drop the session so the next turn starts fresh instead
            # of wedging on the same dead id forever.
            new_session = ""
        return CodingAgentLaunchResult(
            ok=False,
            upstream_session_id=new_session,
            reply_text=reply,
            streamed=streamed,
            output_tail=proc.raw_tail,
            error=error,
            status=status,
            blocks=blocks,
        )
    return CodingAgentLaunchResult(
        ok=True,
        upstream_session_id=new_session,
        reply_text=reply,
        streamed=streamed,
        output_tail=proc.raw_tail,
        status="done",
        usage=usage,
        blocks=blocks,
    )
