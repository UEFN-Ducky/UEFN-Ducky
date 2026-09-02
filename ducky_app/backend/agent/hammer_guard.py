"""Hammer guard: stop the agent re-issuing a call that keeps failing identically.

Tracks the last failure per conversation (falls back to a per-process slot when
no conversation is bound). On the THIRD consecutive failure of the same tool
with the same normalized error, the dispatcher swaps the raw error for a STOP
payload that includes the tool's input schema. Any success, or a different
error, resets the streak.
"""

from __future__ import annotations

import json
import re
import threading
from contextvars import ContextVar
from typing import Any

STOP_THRESHOLD = 3
STOP_TEXT = (
    "STOP: this exact call has failed 3 times with the same error. Do not retry it. "
    "Read the schema above, change the arguments or the approach, or ask the user."
)

_conv_id: ContextVar[str] = ContextVar("ducky_hammer_conv_id", default="")
_lock = threading.Lock()
# conv_id -> (streak_key, count)
_streaks: dict[str, tuple[str, int]] = {}

_DIGITS_RE = re.compile(r"\d+")
_QUOTED_RE = re.compile(r"\"[^\"]*\"|'[^']*'")
_WS_RE = re.compile(r"\s+")


def bind_conversation(conv_id: str) -> object:
    """Bind the current task/thread to *conv_id*; returns a token for reset_conversation."""
    return _conv_id.set(str(conv_id or ""))


def reset_conversation(token: object) -> None:
    _conv_id.reset(token)  # type: ignore[arg-type]


def current_conversation() -> str:
    return _conv_id.get()


def normalize_error(text: str) -> str:
    """Digits and quoted strings stripped, lowercased, first 200 chars."""
    s = _QUOTED_RE.sub("", str(text or ""))
    s = _DIGITS_RE.sub("", s)
    s = _WS_RE.sub(" ", s).strip().lower()
    return s[:200]


def streak_key(tool_name: str, error: str) -> str:
    return f"{tool_name}\x00{normalize_error(error)}"


def note_success(tool_name: str) -> None:  # noqa: ARG001 — any success resets the streak
    with _lock:
        _streaks.pop(_conv_id.get(), None)


def note_failure(tool_name: str, error: str) -> int:
    """Record a failure; return the consecutive-identical count (1-based)."""
    key = streak_key(tool_name, error)
    conv = _conv_id.get()
    with _lock:
        prev_key, prev_count = _streaks.get(conv, ("", 0))
        count = prev_count + 1 if prev_key == key else 1
        _streaks[conv] = (key, count)
    return count


def should_stop(count: int) -> bool:
    return count >= STOP_THRESHOLD


def stop_payload(error: str, schema: Any) -> str:
    """Text returned instead of the raw error once the threshold is hit."""
    return json.dumps(
        {"error": error, "input_schema": schema, "STOP": STOP_TEXT},
        ensure_ascii=False,
        default=str,
    )


def reset_all() -> None:
    with _lock:
        _streaks.clear()
