"""Turn events from the host to the app: text joined to at most 10 batches per second per chat.

``--include-partial-messages`` makes every token an event. The host's main loop feeds each one
to ``Batcher.feed`` and calls ``flush_due`` about every 50 ms; ``emit(conv_id, events)`` gets a
batch, e.g. one ``text_delta`` carrying 100 tokens, and sends it as one ``event`` frame.

Text, thinking and status lines wait for the next slot; anything else (tool start/done, errors,
turn end) goes at once, after the text before it. Chats not on screen (``set_visible``) get no
text, only one status line per second: the running tool and how long it has run.

Only a turn's own events (text, thinking, status, tool calls) make a chat busy; an error or
turn end stops its status line, and so do 2 minutes with no event (a turn that died without
``agent_stopped``) or a tool call past 30 minutes. A chat-list or link event never does: those
once kept a status line ticking for hours from a clock that had never started.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

FOLD_TYPES = frozenset({"text_delta", "thinking", "status"})
HIDDEN_FOLD_TYPES = FOLD_TYPES | {"tool", "tool_done"}
END_TYPES = frozenset({"agent_stopped"})
QUIET_TYPES = END_TYPES | {"error"}
BUSY_TYPES = HIDDEN_FOLD_TYPES
SILENT_AFTER_S = 120.0
TOOL_CAP_S = 1800.0

Emit = Callable[[str, list[dict[str, Any]]], None]


def coalesce(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join adjacent text/thinking chunks of one run; a newer status line replaces the last.

    Same rule as panel_api._coalesce_agent_events (copied: importing panel_api pulls in the UI).
    """
    out: list[dict[str, Any]] = []
    for event in events:
        current = dict(event)
        kind = str(current.get("type") or "")
        if out:
            previous = out[-1]
            same_run = (
                previous.get("conv_id") == current.get("conv_id")
                and previous.get("run_id") == current.get("run_id")
            )
            if same_run and kind in ("text_delta", "thinking") and previous.get("type") == kind:
                previous["text"] = str(previous.get("text") or "") + str(current.get("text") or "")
                continue
            if same_run and kind == "status" and previous.get("type") == "status":
                out[-1] = current
                continue
        out.append(current)
    return out


@dataclass
class _Chat:
    pending: list[dict[str, Any]] = field(default_factory=list)
    due: float = 0.0
    last_emit: float = float("-inf")
    run_id: str = ""
    run_started: float = 0.0
    tool: str = ""
    tool_started: float = 0.0
    last_status: float = float("-inf")
    last_event: float = 0.0
    active: bool = False


class Batcher:
    def __init__(
        self,
        emit: Emit,
        *,
        per_second: float = 10.0,
        status_every_s: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._emit = emit
        self._gap = 1.0 / per_second
        self._status_every_s = status_every_s
        self._clock = clock
        self._chats: dict[str, _Chat] = {}
        self._visible: set[str] | None = None  # None: every chat is on screen
        self._lock = threading.Lock()

    def set_visible(self, chat_ids: list[str] | set[str] | None) -> None:
        """Chats on screen get live text; None means all. Text already waiting is sent first."""
        now = self._clock()
        with self._lock:
            self._visible = None if chat_ids is None else set(chat_ids)
            for conv_id, chat in list(self._chats.items()):
                if not self._is_visible(conv_id):
                    self._flush(conv_id, chat, now)
                    chat.last_status = float("-inf")

    def feed(self, event: dict[str, Any]) -> None:
        conv_id = str(event.get("conv_id") or "")
        kind = str(event.get("type") or "")
        now = self._clock()
        with self._lock:
            chat = self._chats.setdefault(conv_id, _Chat())
            self._track(chat, event, kind, now)
            if not self._is_visible(conv_id):
                if kind in HIDDEN_FOLD_TYPES:
                    if chat.active and now - chat.last_status >= self._status_every_s:
                        self._send_status(conv_id, chat, now)
                    return
                chat.pending.append(event)
                self._flush(conv_id, chat, now)
            else:
                if not chat.pending:
                    chat.due = max(now, chat.last_emit) + self._gap
                chat.pending.append(event)
                if kind not in FOLD_TYPES:
                    self._flush(conv_id, chat, now)
            if kind in END_TYPES and not chat.pending:
                self._chats.pop(conv_id, None)

    def flush_due(self) -> None:
        """Send text whose slot has come, and the once-a-second status of hidden busy chats."""
        now = self._clock()
        with self._lock:
            for conv_id, chat in list(self._chats.items()):
                if chat.pending and now >= chat.due:
                    self._flush(conv_id, chat, now)
                elif chat.active and self._went_quiet(chat, now):
                    chat.active, chat.tool = False, ""
                elif (
                    chat.active
                    and not self._is_visible(conv_id)
                    and now - chat.last_status >= self._status_every_s
                ):
                    self._send_status(conv_id, chat, now)
                if not chat.active and not chat.pending:
                    self._chats.pop(conv_id, None)

    def flush(self) -> None:
        """Send everything waiting now (host shutdown)."""
        now = self._clock()
        with self._lock:
            for conv_id, chat in list(self._chats.items()):
                self._flush(conv_id, chat, now)

    def _is_visible(self, conv_id: str) -> bool:
        return self._visible is None or conv_id in self._visible

    @staticmethod
    def _went_quiet(chat: _Chat, now: float) -> bool:
        if chat.tool:
            return now - chat.tool_started > TOOL_CAP_S
        return now - chat.last_event > SILENT_AFTER_S

    @staticmethod
    def _track(chat: _Chat, event: dict[str, Any], kind: str, now: float) -> None:
        run_id = str(event.get("run_id") or "")
        if run_id and run_id != chat.run_id:
            chat.run_id, chat.run_started, chat.tool = run_id, now, ""
        if kind in QUIET_TYPES:
            chat.active, chat.tool = False, ""
            return
        if kind not in BUSY_TYPES:
            return
        if not chat.active:
            chat.active, chat.run_started = True, now
        chat.last_event = now
        if kind == "tool":
            tool = event.get("tool") if isinstance(event.get("tool"), dict) else {}
            chat.tool, chat.tool_started = str(tool.get("name") or "tool"), now
        elif kind == "tool_done":
            chat.tool = ""

    def _flush(self, conv_id: str, chat: _Chat, now: float) -> None:
        if not chat.pending:
            return
        batch, chat.pending = coalesce(chat.pending), []
        chat.last_emit = now
        self._emit(conv_id, batch)

    def _send_status(self, conv_id: str, chat: _Chat, now: float) -> None:
        chat.last_status = now
        since = chat.tool_started if chat.tool else chat.run_started
        elapsed = max(0, int(now - since))
        label = chat.tool or "Working"
        self._emit(
            conv_id,
            [
                {
                    "type": "status",
                    "conv_id": conv_id,
                    "run_id": chat.run_id,
                    "text": f"{label} · {elapsed}s",
                    "tool": chat.tool,
                    "elapsed_s": elapsed,
                    "heartbeat": True,
                }
            ],
        )
