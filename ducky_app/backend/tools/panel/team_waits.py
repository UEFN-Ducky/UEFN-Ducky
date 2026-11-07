"""Bounded waits for team and window tools: each call returns inside the 5-minute stuck mark.

Nov 7 2026: ducky_recycle_member summed a 600 s hand-off, a 600 s idle poll and a 900 s
reply wait (an external CLI member: no limit at all); ducky_agent_send and uefn_popups had
no bound on their broker / window round trips, so a busy broker or a busy UEFN editor
thread held the agent's whole turn.
"""
from __future__ import annotations

import contextvars
import functools
import threading
import time
from typing import Any, Callable

from backend.util.json_util import tool_json

#: One tool call's whole wall clock, under the 5-minute "stuck" mark.
TOOL_BUDGET_S = 240.0
#: The recycle hand-off turn only writes a summary.
HANDOFF_MAX_S = 120.0


class Budget:
    """What is left of one call's TOOL_BUDGET_S."""

    def __init__(self, total: float = TOOL_BUDGET_S) -> None:
        self.until = time.monotonic() + float(total)

    def left(self, floor: float = 5.0) -> float:
        return max(floor, self.until - time.monotonic())

    def handoff(self, asked: float) -> float:
        return max(30.0, min(float(asked), HANDOFF_MAX_S, self.left()))

    def reply(self, asked: float) -> float:
        return max(5.0, min(float(asked), self.left()))


def wait_idle(conv_id: str, seconds: float) -> None:
    """Poll until ``conv_id``'s run is idle, at most ``seconds``."""
    from frontend.ui_web.agent_turns.state import is_agent_running

    by = time.monotonic() + float(seconds)
    while is_agent_running(conv_id) and time.monotonic() < by:
        time.sleep(0.5)


def call_within(seconds: float, fn: Callable[..., Any], *args: Any, fallback: Callable[[], Any], **kwargs: Any) -> Any:
    """``fn(*args, **kwargs)`` on a worker thread (same context vars: chat identity, mode).
    Past ``seconds`` returns ``fallback()``; the worker finishes on its own."""
    done = threading.Event()
    box: dict[str, Any] = {}
    ctx = contextvars.copy_context()

    def run() -> None:
        try:
            box["value"] = ctx.run(fn, *args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 — re-raised on the caller's thread
            box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=run, daemon=True, name=f"bounded-{getattr(fn, '__name__', 'call')}").start()
    if not done.wait(float(seconds)):
        return fallback()
    if "error" in box:
        raise box["error"]
    return box["value"]


def bounded(seconds: float, message: str) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """A tool that answers within ``seconds``: past it the call returns
    ``{"ok": false, "status": "timeout", "error": message}`` (``functools.wraps`` keeps the
    signature and docstring FastMCP reads)."""

    def wrap(fn: Callable[..., str]) -> Callable[..., str]:
        @functools.wraps(fn)
        def call(*args: Any, **kwargs: Any) -> str:
            def late() -> str:
                out = {"ok": False, "status": "timeout", "seconds": float(seconds), "error": message}
                return tool_json(out, pretty=bool(kwargs.get("pretty")))

            return call_within(seconds, fn, *args, fallback=late, **kwargs)

        return call

    return wrap


AGENT_SEND_S = 20.0
AGENT_SEND_LATE = (
    "The message broker did not confirm within 20 s (the Ducky window is busy). The message may "
    "still be delivered: do not send it again (a repeat is dropped). End your turn; a reply "
    "arrives as a [ducky:agent-message]."
)
POPUPS_S = 20.0
POPUP_CAPTURE_S = 6.0
POPUPS_LATE = (
    "UEFN's windows did not answer within 20 s: its editor thread is busy. "
    "Try uefn_window_list, then uefn_window_capture on the popup."
)
