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
  Another chat's mutating call waits its turn (never refused) and runs once the lease is
  free. Reads always pass, and so do calls with no chat (the person, the panel). The lease
  lives in a file in Ducky's app data because every agent can run in its own process.
* **Editor process** -- an agent's launch / close / restart of UEFN asks the person first
  (:func:`call_editor_process`); a no or a timeout cancels it. Each one, the person's
  own included, appends ``time, chat id, reason`` to the open project's memory entry
  :data:`CRASH_WATCH_ENTRY`, so a reopened editor is never mistaken for a crash.
* **Memory** is never a reason to refuse: a heavy call (:func:`is_heavy_op`) only anchors
  the baseline :func:`uefn_growth` measures from (UEFN's size at its first heavy call, per
  UEFN process), which the status shows. Memory is read only here, never polled. After a
  heavy call answers on a UEFN grown past the limit, :func:`free_uefn_memory` runs once in
  the background (one at a time, at most every :data:`FREE_GAP_SEC`); it never delays a call.
* **No deadline** -- a UEFN call waits while UEFN works. It ends when UEFN answers, its
  connection drops (UEFN closed), or the person presses Stop. Only a caller's own
  ``timeout`` stops it early (:class:`EditorTimeout`). UEFN is never killed from here.
  Epic UEFN calls get one in ``client_pool`` (``call_limits.epic_call_limit``): the agent
  hears why, the editor's work is never cancelled.
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


class EditorProcessRefused(RuntimeError):
    """The person said no to (or never answered) an agent's UEFN launch / close / restart."""


class EditorTimeout(TimeoutError):
    """UEFN did not answer within the caller's own ``timeout``."""


# How often a call waiting for another chat's lease looks again.
LEASE_POLL_SEC = 0.5
_sleep = time.sleep


_GB = 1024 ** 3
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


def try_lease(chat_id: str, op: str) -> bool:
    """Take or renew the editor lease for ``chat_id``; False while another chat holds it."""
    if not chat_id:
        return True
    with _lease_lock:
        held = lease_holder()
        now = _now()
        if held and held.get("chat_id") != chat_id:
            return False
        since = float(held.get("since") or now) if held else now
        title = held.get("title") if held else _chat_title(chat_id)
        _write_lease({"chat_id": chat_id, "title": title, "since": since, "at": now, "op": op})
        return True


def take_lease(chat_id: str, op: str) -> None:
    """Take the editor lease for ``chat_id``, waiting while another chat holds it."""
    # ponytail: polls the shared lease file, not a strict arrival-order queue; a ticket
    # list in the lease file if two waiting chats ever need first-come-first-served.
    while not try_lease(chat_id, op):
        _sleep(LEASE_POLL_SEC)


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


def uefn_growth() -> dict[str, float] | None:
    """How much the running UEFN grew since its island loaded (first guarded):
    ``{grown_gb, limit_gb}``, or None when UEFN isn't running or can't be read."""
    try:
        uefn = _uefn_memory()
        if uefn is None:
            return None
        pid, started, private = uefn
        grown = (private - _baseline_for(pid, started, private)) / _GB
        return {"grown_gb": round(grown, 1), "limit_gb": _growth_limit_gb()}
    except Exception:
        return None


def _note_heavy(op: str, heavy: bool | None) -> None:
    """A heavy call anchors the growth baseline; never refuses."""
    if is_heavy_op(op) if heavy is None else heavy:
        uefn_growth()


# Measured on a 25.6 GB UEFN (27k actors): EmptyWorkingSet gave the PC 4-8 GB of RAM back;
# collect_garbage, Mem.Trim and TRANSACTION RESET freed under 0.2 GB, so none of them run.
FREED_FILE = "uefn_memory_freed.json"
FREE_GAP_SEC = 60.0
_free_lock = threading.Lock()
_FREE_NOTE = (
    "Moved UEFN's idle memory out of RAM (Windows EmptyWorkingSet): the PC gets that RAM back, "
    "UEFN pages back in what it touches. UEFN's private bytes stay the same; only reopening UEFN "
    "lowers them. Garbage collection and the undo-buffer reset free under 0.2 GB, so they are not "
    "run and the undo history is left as it is."
)


def _working_set(pid: int) -> int:
    import psutil

    return int(psutil.Process(pid).memory_info().rss)


def _empty_working_set(pid: int) -> bool:
    """Windows EmptyWorkingSet on ``pid``: its pages leave RAM, its memory stays intact."""
    import ctypes
    from ctypes import wintypes

    kernel32, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    psapi.EmptyWorkingSet.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(0x0400 | 0x0100, False, pid)  # QUERY_INFORMATION | SET_QUOTA
    if not handle:
        return False
    try:
        return bool(psapi.EmptyWorkingSet(handle))
    finally:
        kernel32.CloseHandle(handle)


def _freed_path() -> Path:
    from frontend.settings import default_app_data_dir

    return Path(default_app_data_dir()) / FREED_FILE


def last_freed() -> dict[str, Any]:
    """The last :func:`free_uefn_memory` result ``{freed_gb, at, pid, why}``, or {}."""
    try:
        data = json.loads(_freed_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def free_uefn_memory(why: str = "on request") -> dict[str, Any]:
    """Give the PC back the RAM UEFN holds but isn't using; reports the GB freed. Never
    touches UEFN's objects, level or undo history, so it is safe while UEFN works."""
    uefn = _uefn_memory()
    if uefn is None:
        return {"ok": False, "error": "UEFN isn't running."}
    pid, _started, private = uefn
    before = _working_set(pid)
    if not _empty_working_set(pid):
        return {"ok": False, "error": "Windows refused to trim UEFN's memory."}
    after = _working_set(pid)
    record = {
        "freed_gb": round(max(0, before - after) / _GB, 1),
        "at": _now(),
        "pid": pid,
        "why": why,
    }
    try:
        path = _freed_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record), encoding="utf-8")
    except OSError:
        pass
    return {
        "ok": True,
        **record,
        "working_set_gb": {"before": round(before / _GB, 1), "after": round(after / _GB, 1)},
        "private_gb": round(private / _GB, 1),
        "note": _FREE_NOTE,
    }


def _spawn(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="uefn-free-memory", daemon=True).start()


def _after_heavy(op: str, heavy: bool | None) -> None:
    """After a heavy call answered: free UEFN's idle RAM in the background when UEFN grew
    past the limit. One at a time, at most every FREE_GAP_SEC; never raises."""
    try:
        if not (is_heavy_op(op) if heavy is None else heavy):
            return
        growth = uefn_growth()
        if not growth or growth["grown_gb"] <= growth["limit_gb"]:
            return
        if _now() - float(last_freed().get("at") or 0) < FREE_GAP_SEC:
            return
        if not _free_lock.acquire(blocking=False):
            return
    except Exception:
        return

    def _run() -> None:
        try:
            free_uefn_memory("after the last heavy call")
        except Exception:
            pass
        finally:
            _free_lock.release()

    _spawn(_run)


def uefn_memory_status() -> dict[str, Any] | None:
    """Growth plus the last free for ``ducky_get_status``; None when UEFN isn't running."""
    growth = uefn_growth()
    if growth is None:
        return None
    out: dict[str, Any] = dict(growth)
    line = f"UEFN +{growth['grown_gb']} GB since load"
    freed = last_freed()
    uefn = _uefn_memory()
    if freed and uefn and freed.get("pid") == uefn[0]:
        out["last_freed"] = freed
        line += f", freed {freed.get('freed_gb')} GB RAM {freed.get('why') or ''}".rstrip()
    out["line"] = line
    return out


def call(chat_id: str, op: str, mutating: bool, fn: Callable[[], T], *, heavy: bool | None = None) -> T:
    """Run one editor call ``fn`` for ``chat_id``; a mutating one waits for the lease first.

    ``heavy`` overrides :func:`is_heavy_op` (a caller that knows the call's arguments)."""
    if mutating:
        take_lease(chat_id, op)
    _note_heavy(op, heavy)
    result = fn()
    _after_heavy(op, heavy)
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


async def acall(
    chat_id: str,
    op: str,
    mutating: bool,
    fn: Callable[[], Awaitable[Any]],
    *,
    heavy: bool | None = None,
    timeout: float | None = None,
) -> Any:
    """:func:`call` for a coroutine: waits for the lease without blocking the loop (Stop
    cancels the wait), then awaits ``fn()`` until UEFN answers. Only the caller's own
    ``timeout`` stops it early (:class:`EditorTimeout`)."""
    if mutating:
        while not try_lease(chat_id, op):
            await asyncio.sleep(LEASE_POLL_SEC)
    _note_heavy(op, heavy)
    result = fn()
    if inspect.isawaitable(result):
        if timeout is None or timeout <= 0:
            result = await result
        else:
            try:
                result = await asyncio.wait_for(result, timeout)
            except asyncio.TimeoutError:
                if chat_id:
                    release_lease(chat_id)
                raise EditorTimeout(_timeout_message(op, timeout)) from None
    _after_heavy(op, heavy)
    return result
