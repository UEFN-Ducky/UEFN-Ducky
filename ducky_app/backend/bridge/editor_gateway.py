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
* **Editor process** -- an agent's launch / close / restart of UEFN asks the person first
  (:func:`call_editor_process`); a no or a timeout cancels it. Each one, the person's
  own included, appends ``time, chat id, reason`` to the open project's memory entry
  :data:`CRASH_WATCH_ENTRY`, so a reopened editor is never mistaken for a crash.
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


CRASH_WATCH_ENTRY = "crash-watch"
_PROCESS_VERBS = {
    "ducky_restart_uefn": "restart",
    "ducky_launch_uefn": "launch",
    "ducky_close_uefn": "close",
}


class EditorBusy(RuntimeError):
    """Another chat holds the editor lease."""


class EditorProcessRefused(RuntimeError):
    """The person said no to (or never answered) an agent's UEFN launch / close / restart."""


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


def _ask_person(chat_id: str, verb: str, reason: str) -> bool:
    """The person's yes on the agent's ask-user card. A no, a skip or a timeout is False."""
    from backend.tools.panel.panel_ui import ducky_ask_user

    why = f": {reason}" if reason else " (no reason given)"
    question = {
        "id": "editor_process",
        "prompt": f"{_chat_title(chat_id)} wants to {verb} UEFN{why}. Allow it?",
        "options": [
            {"id": "yes", "label": f"Yes, {verb} UEFN"},
            {"id": "no", "label": "No, leave UEFN as it is"},
        ],
        "allow_multiple": False,
        "allow_free_text": False,
        "required": True,
    }
    try:
        out = json.loads(ducky_ask_user([question], title=f"{verb.capitalize()} UEFN?"))
    except Exception:
        return False
    answer = ((out or {}).get("answers") or {}).get("editor_process") or {}
    return not out.get("error") and "yes" in (answer.get("selected") or [])


def _log_crash_watch(line: str) -> None:
    """One line in the open project's ``crash-watch`` memory entry. Never raises."""
    try:
        from backend.memory.project import append_entry

        append_entry(CRASH_WATCH_ENTRY, line, author="editor gateway", project_root="")
    except Exception:
        pass


def call_editor_process(chat_id: str, op: str, reason: str, fn: Callable[[], T]) -> T:
    """Launch / close / restart UEFN through the gateway.

    An agent's call (``chat_id`` set) needs the person's yes first and the editor lease;
    the person's own call (no chat) runs at once. Each one is logged to ``crash-watch``.
    """
    verb = _PROCESS_VERBS.get(op, op)
    who = chat_id[:8] if chat_id else "the person"
    stamp = time.strftime("%H:%M", time.localtime(_now()))
    head = f"{stamp} {verb} asked by {who}: {reason!r}" if reason else f"{stamp} {verb} asked by {who}"
    if chat_id:
        take_lease(chat_id, op)
        if not _ask_person(chat_id, verb, reason):
            _log_crash_watch(f"{head} -> person said no (or did not answer)")
            raise EditorProcessRefused(
                f"`{op}` cancelled: the person did not allow it. UEFN was left as it is. "
                "Do not ask again for the same reason; carry on with work that does not need it."
            )
        _log_crash_watch(f"{head} -> person said yes")
    else:
        _log_crash_watch(f"{head} -> by the person")
    return call(chat_id, op, True, fn)


async def acall(chat_id: str, op: str, mutating: bool, fn: Callable[[], Awaitable[Any]]) -> Any:
    """:func:`call` for a coroutine: ``fn()`` returns an awaitable, which is awaited."""
    _check(chat_id, op, mutating)
    result = fn()
    if inspect.isawaitable(result):
        return await result
    return result
