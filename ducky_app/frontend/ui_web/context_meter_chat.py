"""What the context meter needs from a chat's messages, kept per message revision.

The meter runs after every turn and every pause in typing, and each time it opened the
whole chat (a DPAPI call per row and part) and counted every message again: 11% of the
app's CPU with one 130 MB chat open (Nov 11 2026). Those numbers change only when the
chat's rows change, so they are kept under the chat's message revision: a read that finds
them opens no message at all.
"""

from __future__ import annotations

import dataclasses
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from frontend.chat_store import Conversation

MAX_CHATS = 8
# Nothing kept is older than this, whatever else it depends on (app state no key names).
TTL_S = 600.0
_lock = threading.Lock()
# (app data folder, chat id) -> (message revision, kept at, {what: value})
_facts: OrderedDict[tuple[str, str], tuple[int, float, dict[Any, Any]]] = OrderedDict()

LoadChat = Callable[[str], Any]


class MeterConversation(Conversation):
    """A chat loaded without its messages. They are read the first time something asks,
    so a helper that still reads ``conv.messages`` gets the real rows, never an empty list."""

    _read_messages: Callable[[], list[dict[str, Any]]] | None = None


def _meter_messages(self: MeterConversation) -> list[dict[str, Any]]:
    read = self._read_messages
    if read is not None:
        self._read_messages = None
        self._messages = list(read())
    return self._messages


MeterConversation.messages = property(_meter_messages, Conversation.messages.fset)  # type: ignore[assignment, attr-defined]


class MeterChat:
    """One meter read of a chat: ``conv`` for its settings, and :meth:`fact` for what comes
    from its messages (read only when a number isn't kept for this revision)."""

    def __init__(self, conv_id: str, load: LoadChat) -> None:
        self.conv_id = conv_id
        self.conv: Any = None
        self._load = load
        self._key: tuple[str, str] | None = None  # None: nothing is kept for this read
        self._rev = 0
        self._kept: dict[Any, Any] = {}
        self._messages: list[dict[str, Any]] | None = None

    @classmethod
    def of(cls, conv: Any) -> MeterChat:
        """A chat already loaded whole (nothing is kept for it)."""
        chat = cls(str(getattr(conv, "id", "") or ""), lambda _cid: conv)
        chat.conv, chat._messages = conv, list(getattr(conv, "messages", None) or [])
        return chat

    def messages(self) -> list[dict[str, Any]]:
        if self._messages is None:
            full = self._load(self.conv_id)
            self._messages = list(getattr(full, "messages", None) or [])
            if self._key is not None and getattr(full, "_msg_rev", None) != self._rev:
                # Rows changed since the revision was read: what is counted now isn't that
                # revision's, so this read keeps nothing.
                self._key = None
                self._kept = {}
        return self._messages

    def fact(
        self,
        what: Any,
        compute: Callable[[list[dict[str, Any]]], Any],
        copy: Callable[[Any], Any] | None = None,
    ) -> Any:
        """``compute(messages)``, from what is kept for this revision when it is there.
        ``copy``: a fresh copy for the caller (a kept list is never handed out to be changed)."""
        if what in self._kept:
            value = self._kept[what]
            return copy(value) if copy is not None else value
        value = compute(self.messages())
        if self._key is not None:
            with _lock:
                entry = _facts.get(self._key)
                if entry is None or entry[0] != self._rev:
                    entry = (self._rev, time.monotonic(), {})
                    _facts[self._key] = entry
                entry[2][what] = value
                _facts.move_to_end(self._key)
                while len(_facts) > MAX_CHATS:
                    _facts.popitem(last=False)
        return copy(value) if copy is not None else value


def open_chat(conv_id: str, load: LoadChat) -> MeterChat:
    """The chat for one meter read. On the row store, with no save of it still on its way,
    that is the chat without its messages plus what is kept for its revision; otherwise
    the whole chat (``load``), as before."""
    chat = MeterChat(conv_id, load)
    try:
        from frontend.app_paths import resolve_app_data_dir
        from frontend.ui_web.chats import conversations as chat_convs, saves as chat_saves, store as chat_store

        if chat_store._use_db():
            writer = chat_saves._WRITER
            waiting = writer is not None and writer.checkpoint_waits(conv_id)
            if not waiting and chat_saves._queued(conv_id) is None:
                rev = chat_store._repo().conv_msg_rev(conv_id)
                meta = chat_convs.load_conversation_meta(conv_id) if rev is not None else None
                if meta is not None:
                    conv = MeterConversation(
                        **{f.name: getattr(meta, f.name) for f in dataclasses.fields(Conversation)}
                    )
                    conv._read_messages = chat.messages
                    chat.conv, chat._rev = conv, int(rev)
                    chat._key = (str(resolve_app_data_dir()), conv_id)
                    with _lock:
                        entry = _facts.get(chat._key)
                        if entry is not None and (entry[0] != chat._rev or time.monotonic() - entry[1] > TTL_S):
                            del _facts[chat._key]
                            entry = None
                        if entry is not None:
                            chat._kept = entry[2]
                            _facts.move_to_end(chat._key)
    except Exception:  # noqa: BLE001 - the meter still answers, from the whole chat
        chat = MeterChat(conv_id, load)
    if chat.conv is None:
        chat.conv = load(conv_id)
        chat._messages = list(getattr(chat.conv, "messages", None) or [])
    return chat


def forget() -> None:
    """Drop everything kept (a sign-out, tests)."""
    with _lock:
        _facts.clear()


def settings_mark(settings: Any) -> int:
    """Changes whenever a setting does: part of what a kept conversation count is kept under
    (compaction, stale-result clearing and token counting all read settings)."""
    try:
        return hash(json.dumps(dataclasses.asdict(settings), sort_keys=True, default=str))
    except Exception:  # noqa: BLE001 - not a dataclass (a test stand-in)
        return hash(repr(settings))
