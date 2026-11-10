"""Shared helpers for streaming-CLI coding-agent plugins.

Plugins shell out via ``run_streaming_process``, parse a JSON event stream
into text/tool blocks, then translate the finished ``ProcResult`` into a
``CodingAgentLaunchResult``. Vendor resume flags stay in the plugin.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from backend.agent.coding_agents.base import CodingAgentLaunchResult
from backend.agent.coding_agents.proc_exec import ProcResult

# Substrings checked against a lowercased error message to classify a failed
# turn as "needs_login" rather than a plain error. Superset of both CLIs'
# marker lists (e.g. "codex login" and "not logged in" already contain
# "login" / "not logged"), so sharing this one set narrows nothing.
LOGIN_MARKERS = ("sign in", "log in", "login", "oauth", "not logged", "authenticate", "/login")


_JSON_RESULT_CAP = 32_000

_log = logging.getLogger(__name__)
# A home folder in a CLI's stderr (C:\Users\name, /home/name, /Users/name) reads as ~.
_HOME_RE = re.compile(r"(?i)\b[a-z]:[\\/]+users[\\/]+[^\\/\s\"']+|/(?:home|users)/[^/\s\"']+")


def exit_code_text(code: int) -> str:
    """An exit code as people search for it: 1 stays 1, 3221225477 reads 0xC0000005."""
    return str(code) if 0 <= code <= 255 else f"0x{code & 0xFFFFFFFF:08X}"


def exit_detail(proc: ProcResult, agent_label: str) -> str:
    """'<label> exited with code N: <last 3 stderr lines>' with home paths as ~, also logged.

    Nov 9 2026: 0-tool turns and crashes after tools ended with an empty or raw-JSON
    detail, so every one of them read as kind:other with nothing to go on.
    """
    lines = [ln.strip() for ln in (proc.stderr_tail or "").splitlines() if ln.strip()][-3:]
    tail = _HOME_RE.sub("~", " | ".join(lines))
    text = f"{agent_label} exited with code {exit_code_text(proc.returncode)}" + (f": {tail}" if tail else "")
    _log.warning("coding agent exit: %s", text)
    return text


def truncate_tool_result(text: str, max_chars: int = 4000) -> str:
    """Trim a tool-result string for the panel UI, flagging truncation.

    A JSON object (web search, with its picture list) stays whole up to 32k
    characters. Cutting it at 4k leaves invalid JSON and drops the pictures.
    """
    text = (text or "").strip()
    if text.startswith("{") and text.endswith("}") and len(text) <= _JSON_RESULT_CAP:
        return text
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
        # Never the raw stdout tail: for a JSON-stream CLI that is transport, not a reason.
        error = error_text or exit_detail(proc, agent_label)
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
        output_tail="",  # JSON events are diagnostics, never an assistant reply.
        status="done",
        usage=usage,
        blocks=blocks,
    )
