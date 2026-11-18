"""Which mode (agent, ask, plan) a shared-MCP call's run is in, from the host's turn checkpoint."""

from __future__ import annotations

import threading
from typing import Any

_RUNS_MAX = 256
# (conv_id, run_id) -> requested mode. A run's mode never changes, yet every call of it loaded and
# scanned the whole chat. Only a found checkpoint is kept: the run row may not be saved yet.
_modes: dict[tuple[str, str], str] = {}
_lock = threading.Lock()


def _checkpoint_mode(conv_id: str, run_id: str) -> str:
    """The run's requested mode from its checkpoint, or '' when none is saved yet."""
    from frontend.ui_web.chats.conversations import load_conversation

    conv = load_conversation(conv_id)
    for message in reversed(getattr(conv, "messages", []) or []):
        if message.get("role") == "assistant" and message.get("run_id") == run_id:
            # Older Agent checkpoints predate requested_mode.
            return message.get("requested_mode") or "agent"
    return ""


def calling_mode(ident: Any) -> str:
    """Read the host-written turn checkpoint, never a client-supplied mode."""
    conv_id = getattr(ident, "conv_id", "")
    run_id = getattr(ident, "run_id", "")
    external = getattr(ident, "coding_agent", "ducky") in {"codex", "claude_code", "cursor"}
    if conv_id and run_id:
        with _lock:
            mode = _modes.get((conv_id, run_id), "")
        if not mode:
            mode = _checkpoint_mode(conv_id, run_id)
            if mode:
                with _lock:
                    _modes[(conv_id, run_id)] = mode
                    while len(_modes) > _RUNS_MAX:
                        _modes.pop(next(iter(_modes)))
        if mode:
            return mode
    if external:
        raise ValueError("Cannot determine calling chat mode: no matching host turn checkpoint")
    return "agent"
