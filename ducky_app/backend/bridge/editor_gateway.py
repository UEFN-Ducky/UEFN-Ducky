"""The one door every UEFN editor call goes through.

Editor work leaves Ducky three ways: listener HTTP commands (``bridge.client``),
Epic's in-editor MCP (``unreal__*``, ``mcp_plugins.epic``) and the editor's own
process (launch / close / restart in ``tools.panel.ducky_panel``). Each of them
calls :func:`call` (or :func:`acall` for coroutines), so a safety rule written
here holds for all three at once.

``op`` names the call (a listener command, ``unreal__<tool>``, ``ducky_restart_uefn``);
``mutating`` is False for reads that never change the editor or the island.

Rules:

* **Editor lease** -- one chat drives UEFN at a time. A mutating call takes the lease
  for its chat and renews it; after :data:`LEASE_IDLE_SEC` without a call it lapses.
  Another chat's mutating call is refused with :class:`EditorBusy`. Reads always pass,
  and so do calls with no chat (the person, the panel). The lease lives in a file in
  Ducky's app data because every agent can run in its own process.
"""

from __future__ import annotations

import inspect
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")

LEASE_IDLE_SEC = 60.0
LEASE_FILE = "editor_lease.json"
_lease_lock = threading.Lock()
# Wall clock, not monotonic: the lease is shared between processes.
_now = time.time


class EditorBusy(RuntimeError):
    """Another chat holds the editor lease."""


def current_chat_id() -> str:
    """The chat whose agent is making this call, '' for the panel or an unknown caller."""
    try:
        from backend.workspace import identity

        # The call's own context, else the agent process Ducky started (its env names the chat).
        ctx = identity.current() or identity.from_env()
    except Exception:
        return ""
    return str(getattr(ctx, "conv_id", "") or "")


def _lease_path() -> Path:
    from frontend.settings import default_app_data_dir

    return Path(default_app_data_dir()) / LEASE_FILE


def _read_lease() -> dict[str, Any]:
    try:
        data = json.loads(_lease_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_lease(data: dict[str, Any]) -> None:
    path = _lease_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # a lease that cannot be saved must never stop the editor call


def _chat_title(chat_id: str) -> str:
    try:
        from frontend.ui_web.chats.conversations import conversation_title

        return conversation_title(chat_id) or chat_id
    except Exception:
        return chat_id


def lease_holder() -> dict[str, Any]:
    """The live lease ``{chat_id, title, since, at}``, or {} when nobody holds it."""
    lease = _read_lease()
    if not lease.get("chat_id") or _now() - float(lease.get("at") or 0) >= LEASE_IDLE_SEC:
        return {}
    return lease


def take_lease(chat_id: str, op: str) -> None:
    """Take or renew the editor lease for ``chat_id``; raise :class:`EditorBusy` if another chat holds it."""
    if not chat_id:
        return
    with _lease_lock:
        held = lease_holder()
        now = _now()
        if held and held.get("chat_id") != chat_id:
            title = held.get("title") or held.get("chat_id")
            seconds = int(now - float(held.get("since") or now))
            raise EditorBusy(
                f"editor busy: held by {title} ({seconds} s). `{op}` not sent: another chat is "
                f"driving UEFN. Do other work and try again once it has been idle for "
                f"{int(LEASE_IDLE_SEC)} s."
            )
        since = float(held.get("since") or now) if held else now
        title = held.get("title") if held else _chat_title(chat_id)
        _write_lease({"chat_id": chat_id, "title": title, "since": since, "at": now, "op": op})


def release_lease(chat_id: str) -> None:
    """Give the lease back early (a chat that finished its editor work)."""
    with _lease_lock:
        if _read_lease().get("chat_id") == chat_id:
            _write_lease({})


def _check(chat_id: str, op: str, mutating: bool) -> None:
    if mutating:
        take_lease(chat_id, op)


def call(chat_id: str, op: str, mutating: bool, fn: Callable[[], T]) -> T:
    """Run one editor call ``fn`` for ``chat_id`` once the gateway's rules let it through."""
    _check(chat_id, op, mutating)
    return fn()


async def acall(chat_id: str, op: str, mutating: bool, fn: Callable[[], Awaitable[Any]]) -> Any:
    """:func:`call` for a coroutine: ``fn()`` returns an awaitable, which is awaited."""
    _check(chat_id, op, mutating)
    result = fn()
    if inspect.isawaitable(result):
        return await result
    return result
