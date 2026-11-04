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
* **Memory guard** -- before a heavy call (:func:`is_heavy_op`: Verse compile/build, a
  play session, an import, ``execute_python``, an unfiltered actor census) the gateway
  reads UEFN's private bytes and the PC's free RAM. It refuses with
  :class:`EditorMemoryLow` when UEFN grew more than ``uefn_memory_growth_limit_gb``
  (default 4) since the island loaded, or the PC has under :data:`MIN_FREE_RAM_GB` free.
  An island alone is ~21 GB, so the guard watches growth, not the total. The baseline
  is UEFN's size at the first guarded call for that UEFN process (pid + start time),
  kept in app data so every agent process shares it, and reset when UEFN restarts.
  Memory is read only here, never polled, and never for other calls.
* **Deadline** -- every :func:`acall` has a time limit: :data:`READ_TIMEOUT_SEC` for reads,
  :data:`MUTATING_TIMEOUT_SEC` for mutating calls (``unreal__call_tool`` included) and
  :data:`SLOW_TIMEOUT_SEC` for calls slow by design (Verse build, a play session, launch /
  restart, a screenshot). A caller's own ``timeout`` wins; ``UEFN_DUCKY_EDITOR_CALL_TIMEOUT_S``
  replaces the read / mutating defaults. On a timeout the lease is released and
  :class:`EditorTimeout` raised; for :data:`HANG_COOLDOWN_SEC` after it, further ``acall``
  calls fail fast with the same message instead of queuing behind the hung one. A successful
  read (any :func:`call` or :func:`acall`) ends that early. UEFN is never killed from here.
"""

from __future__ import annotations

import asyncio
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


class EditorMemoryLow(RuntimeError):
    """UEFN grew too much since the island loaded, or the PC is low on free RAM."""


class EditorTimeout(TimeoutError):
    """UEFN did not answer an editor call within its deadline (or one just hung)."""


READ_TIMEOUT_SEC = 60.0
MUTATING_TIMEOUT_SEC = 180.0
SLOW_TIMEOUT_SEC = 600.0
HANG_COOLDOWN_SEC = 30.0
TIMEOUT_ENV = "UEFN_DUCKY_EDITOR_CALL_TIMEOUT_S"
# Name words of calls that are slow by design (op names are compared in snake_case).
_SLOW_WORDS = ("compile", "build", "launch", "restart", "screenshot", "play", "session", "import")
# Per process: the hang is about this process's queue of calls. Monotonic for the cooldown.
_mono = time.monotonic
_hang_lock = threading.Lock()
_hang: dict[str, Any] = {}


_GB = 1024 ** 3
MIN_FREE_RAM_GB = 4.0
DEFAULT_GROWTH_LIMIT_GB = 4.0
BASELINE_FILE = "uefn_memory_baseline.json"
# Name words of heavy editor calls (op names are compared in snake_case).
_HEAVY_WORDS = (
    "compile",
    "build_verse",
    "verse_build",
    "play_in_editor",
    "start_pie",
    "start_session",
    "launch_session",
    "start_play",
    "import",
    "execute_python",
    "get_all_actors",
)
# A FindActors census is heavy only without a filter: these arguments do not narrow it.
_CENSUS_NON_FILTERS = frozenset({"limit", "max_results", "offset", "fields", "page", "page_size"})


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


def _snake(name: str) -> str:
    import re

    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name or "").lower()


def is_heavy_op(op: str, arguments: dict[str, Any] | None = None) -> bool:
    """True for an editor call that can add GBs to UEFN: Verse compile/build, a play
    session, an import, ``execute_python``, an unfiltered actor census."""
    name = _snake(op)
    if any(word in name for word in _HEAVY_WORDS):
        return True
    if "verse" in name and "build" in name:
        return True
    if "find_actors" in name:
        narrowing = {k: v for k, v in (arguments or {}).items() if k.lower() not in _CENSUS_NON_FILTERS and v}
        return not narrowing
    return False


def _uefn_memory() -> tuple[int, float, int] | None:
    """(pid, start time, private bytes) of the running UEFN, or None (not running / no psutil)."""
    try:
        import psutil
    except ImportError:
        return None
    best: tuple[int, float, int] | None = None
    for proc in psutil.process_iter(["name", "pid"]):
        try:
            if not str(proc.info.get("name") or "").lower().startswith("unrealeditorfortnite"):
                continue
            info = proc.memory_full_info()
            private = int(getattr(info, "private", 0) or getattr(info, "uss", 0) or info.rss)
            if best is None or private > best[2]:
                best = (proc.pid, proc.create_time(), private)
        except Exception:
            continue
    return best


def _pc_free_bytes() -> int | None:
    try:
        import psutil

        return int(psutil.virtual_memory().available)
    except Exception:
        return None


def _growth_limit_gb() -> float:
    try:
        from frontend.settings import PanelSettings

        value = float(getattr(PanelSettings.load(), "uefn_memory_growth_limit_gb", DEFAULT_GROWTH_LIMIT_GB))
        return value if value > 0 else DEFAULT_GROWTH_LIMIT_GB
    except Exception:
        return DEFAULT_GROWTH_LIMIT_GB


def _baseline_path() -> Path:
    from frontend.settings import default_app_data_dir

    return Path(default_app_data_dir()) / BASELINE_FILE


def _baseline_for(pid: int, started: float, private: int) -> int:
    """UEFN's private bytes when this UEFN process was first guarded; set now if new."""
    path = _baseline_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if isinstance(data, dict) and data.get("pid") == pid and float(data.get("started") or 0) == started:
        return int(data.get("private") or private)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"pid": pid, "started": started, "private": private}), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass
    return private


def check_memory(op: str) -> None:
    """Raise :class:`EditorMemoryLow` when a heavy ``op`` would push UEFN or the PC too far."""
    uefn = _uefn_memory()
    free = _pc_free_bytes()
    growth = 0.0
    if uefn is not None:
        pid, started, private = uefn
        growth = (private - _baseline_for(pid, started, private)) / _GB
    free_gb = free / _GB if free is not None else None
    limit = _growth_limit_gb()
    if growth > limit or (free_gb is not None and free_gb < MIN_FREE_RAM_GB):
        free_text = f"{free_gb:.1f} GB" if free_gb is not None else "unknown"
        raise EditorMemoryLow(
            f"UEFN grew {growth:.1f} GB since the island loaded (PC free {free_text}): save and "
            f"restart UEFN first (ask the person). `{op}` not sent; the limits are "
            f"{limit:g} GB of growth and {MIN_FREE_RAM_GB:g} GB free RAM."
        )


def _check(chat_id: str, op: str, mutating: bool, heavy: bool | None = None) -> None:
    if mutating:
        take_lease(chat_id, op)
    if is_heavy_op(op) if heavy is None else heavy:
        check_memory(op)


def call(chat_id: str, op: str, mutating: bool, fn: Callable[[], T], *, heavy: bool | None = None) -> T:
    """Run one editor call ``fn`` for ``chat_id`` once the gateway's rules let it through.

    ``heavy`` overrides :func:`is_heavy_op` (a caller that knows the call's arguments)."""
    _check(chat_id, op, mutating, heavy)
    result = fn()
    if not mutating:
        clear_hang()
    return result


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


def _timeout_message(op: str, seconds: float) -> str:
    return (
        f"UEFN didn't answer {op} in {seconds:g}s: a popup may be open (uefn_popups), "
        "or the editor is busy"
    )


def _env_timeout() -> float | None:
    try:
        value = float(os.environ.get(TIMEOUT_ENV, "") or 0)
    except ValueError:
        return None
    return value if value > 0 else None


def call_timeout(op: str, mutating: bool, heavy: bool | None = None, timeout: float | None = None) -> float:
    """The deadline in seconds for one :func:`acall`."""
    if timeout is not None and timeout > 0:
        return float(timeout)
    name = _snake(op)
    slow = bool(heavy) or any(word in name for word in _SLOW_WORDS)
    override = _env_timeout()
    if slow:
        return max(SLOW_TIMEOUT_SEC, override or 0.0)
    if override is not None:
        return override
    return MUTATING_TIMEOUT_SEC if mutating or name.endswith("call_tool") else READ_TIMEOUT_SEC


def hang_state() -> dict[str, Any]:
    """The live hang ``{op, seconds, message, until}``, or {} when no call hung recently."""
    with _hang_lock:
        if _hang and _mono() < float(_hang.get("until") or 0):
            return dict(_hang)
        _hang.clear()
        return {}


def clear_hang() -> None:
    """End the fail-fast window (an editor call just succeeded)."""
    with _hang_lock:
        _hang.clear()


def _mark_hang(op: str, seconds: float, message: str) -> None:
    with _hang_lock:
        _hang.update({"op": op, "seconds": seconds, "message": message, "until": _mono() + HANG_COOLDOWN_SEC})


async def acall(
    chat_id: str,
    op: str,
    mutating: bool,
    fn: Callable[[], Awaitable[Any]],
    *,
    heavy: bool | None = None,
    timeout: float | None = None,
) -> Any:
    """:func:`call` for a coroutine: ``fn()`` returns an awaitable, awaited within its deadline
    (:func:`call_timeout`). Raises :class:`EditorTimeout` when UEFN does not answer in time,
    and at once while an earlier call's hang is still fresh."""
    hung = hang_state()
    if hung:
        raise EditorTimeout(hung["message"])
    _check(chat_id, op, mutating, heavy)
    result = fn()
    if inspect.isawaitable(result):
        seconds = call_timeout(op, mutating, heavy, timeout)
        try:
            result = await asyncio.wait_for(result, seconds)
        except asyncio.TimeoutError:
            message = _timeout_message(op, seconds)
            _mark_hang(op, seconds, message)
            if chat_id:
                release_lease(chat_id)
            raise EditorTimeout(message) from None
    if not mutating:
        clear_hang()
    return result
