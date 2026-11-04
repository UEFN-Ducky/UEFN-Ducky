"""Start a team member's turn only when the PC has memory for it (decision D2).

Agents kept starting while the PC had 0.1 GB free, and the window process died out of
memory. A sub-lead's or member's turn now waits in one FIFO line until starting it leaves at
least ``agent_min_free_ram_gb`` (default 6 GB) free, or fewer than ``FLOOR`` member turns run
(so a team always moves). Starting one costs what an agent measurably uses: the rolling
average of the live agent processes (claude.exe, node, its bridges) per running agent, not a
constant. Agents started in the last ``RESERVE_S`` are counted as already using it, since a
new agent takes a while to load. Running agents are never paused or stopped: only new starts
wait ("Waiting for memory (N GB free)").
Never queued: a person's own send, Stop, question answers, the top lead of a group tree and
chats outside any group. An admitted turn holds its slot until it ends (``release``), also
while it waits on teammates. The line is re-checked every ``RECHECK_S`` and whenever a turn
ends; after ``NOTICE_AFTER_S`` a waiting chat is told which processes hold the memory.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

START_FREE_GB = 6.0  # default for the agent_min_free_ram_gb setting
FLOOR = 2
# Until agents run to measure: a real coding agent is ~230 MB plus node and two bridges.
DEFAULT_AGENT_GB = 0.3
RESERVE_S = 30.0
COST_EVERY_S = 10.0
_COST_ALPHA = 0.3
# Processes a coding agent brings; the first group counts agents.
_AGENT_HEADS = frozenset({"claude.exe", "codex.exe", "opencode.exe", "cursor-agent.exe"})
_AGENT_PARTS = _AGENT_HEADS | frozenset({"node.exe", "uefn-ducky-bridge.exe"})
RECHECK_S = 5.0
NOTICE_AFTER_S = 300.0
_ROLE_TTL_S = 10.0

PushFn = Callable[[dict[str, Any]], None]


@dataclass
class _Waiter:
    conv_id: str
    start: Callable[[], Any]
    push: PushFn | None
    since: float
    noticed: bool = False
    shown: int = field(default=0)  # the position last pushed to the chat


_lock = threading.Lock()
_wake = threading.Event()
_waiting: list[_Waiter] = []
_running: set[str] = set()  # member / sub-lead turns holding a slot
_roles: dict[str, tuple[float, str]] = {}
_thread: threading.Thread | None = None
_now = time.monotonic
_admits: deque[float] = deque()  # when recent member turns were let in (not yet loaded)
_cost: dict[str, float] = {"gb": 0.0, "at": float("-inf")}
_floor_cache: dict[str, float] = {"gb": START_FREE_GB, "at": float("-inf")}


def free_memory_gb() -> float:
    """Physical memory free right now, in GB (GlobalMemoryStatusEx; psutil elsewhere)."""
    try:
        import ctypes
        from ctypes import wintypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(stat)):
            return stat.ullAvailPhys / 1024**3
    except (AttributeError, OSError):
        pass
    try:
        import psutil

        return psutil.virtual_memory().available / 1024**3
    except Exception:
        return float("inf")  # can't tell: never hold work back on a guess


_read_free = free_memory_gb


def min_free_gb() -> float:
    """The ``agent_min_free_ram_gb`` setting (env ``UEFN_DUCKY_AGENT_MIN_FREE_RAM_GB`` wins)."""
    now = _now()
    if now - _floor_cache["at"] < COST_EVERY_S:
        return _floor_cache["gb"]
    gb = START_FREE_GB
    raw = os.environ.get("UEFN_DUCKY_AGENT_MIN_FREE_RAM_GB")
    try:
        if raw:
            gb = float(raw)
        else:
            from frontend.settings import PanelSettings

            value = getattr(PanelSettings.load(), "agent_min_free_ram_gb", None)
            if value:
                gb = float(value)
    except Exception:
        pass
    _floor_cache.update(gb=gb, at=now)
    return gb


def _measure_cost() -> float | None:
    """GB the live coding agents hold, per agent; None when no agent runs."""
    from backend.util import proc_table

    heads = 0
    held = 0
    for proc in proc_table.table().values():
        name = (proc.name or "").lower()
        if name in _AGENT_PARTS:
            held += int(proc.private)
            heads += name in _AGENT_HEADS
    return held / heads / 1024**3 if heads else None


def agent_cost_gb() -> float:
    """What one more agent will use: a rolling average of the measured per-agent memory."""
    now = _now()
    if now - _cost["at"] >= COST_EVERY_S:
        _cost["at"] = now
        try:
            sample = _measure_cost()
        except Exception:
            sample = None
        if sample:
            _cost["gb"] = sample if not _cost["gb"] else _cost["gb"] + _COST_ALPHA * (sample - _cost["gb"])
    return _cost["gb"] or DEFAULT_AGENT_GB


def _loading() -> int:
    """Member turns let in during the last RESERVE_S (their memory is not in the free count yet)."""
    floor = _now() - RESERVE_S
    while _admits and _admits[0] < floor:
        _admits.popleft()
    return len(_admits)


def has_room(running: int, extra: int = 0) -> bool:
    """Room for one more member turn: under ``FLOOR``, or the start leaves the memory floor free.

    ``extra``: turns being started now that admission has not counted yet (the seat queue's).
    """
    if running < FLOOR:
        return True
    cost = agent_cost_gb()
    return _read_free() - (_loading() + extra + 1) * cost >= min_free_gb()


def _admit(conv_id: str) -> None:
    """Call under ``_lock``."""
    _running.add(conv_id)
    _admits.append(_now())


def _role(conv_id: str) -> str:
    # ponytail: 10 s cache; the group scan reads every chat's metadata, too slow per turn start.
    hit = _roles.get(conv_id)
    now = _now()
    if hit and now - hit[0] < _ROLE_TTL_S:
        return hit[1]
    try:
        from backend.agent.a2a_broker import team_role

        role = team_role(conv_id)
    except Exception:
        role = "solo"
    _roles[conv_id] = (now, role)
    return role


def _queues(conv_id: str, source: str) -> bool:
    return source not in ("person", "stop", "answer") and _role(conv_id) in ("sub_lead", "member")


def _room() -> bool:
    """Call under ``_lock``."""
    return has_room(len(_running))


def may_start(conv_id: str, source: str, *, start: Callable[[], Any] | None = None,
              push: PushFn | None = None) -> bool:
    """True: start the turn now. False: it waits in line and ``start`` runs once admitted.

    ``source``: ``person`` / ``stop`` / ``answer`` never wait; anything else (``agent``) may.
    """
    if not _queues(conv_id, source):
        return True
    with _lock:
        if not _waiting and _room():
            _admit(conv_id)
            return True
        if start is None:
            return False
        _waiting.append(_Waiter(conv_id, start, push, _now()))
    _show_positions()
    _ensure_thread()
    return False


def holds(conv_id: str) -> bool:
    """True when the chat holds a member slot (it must ``release`` it when its turn ends)."""
    with _lock:
        return conv_id in _running


def release(conv_id: str) -> None:
    """The chat's turn ended (or never started): free its slot and let the line move."""
    with _lock:
        had = conv_id in _running
        _running.discard(conv_id)
        waiting = bool(_waiting)
    if had and waiting:
        drain()


def drop(conv_id: str) -> None:
    """Take the chat out of the line (Stop)."""
    with _lock:
        _waiting[:] = [w for w in _waiting if w.conv_id != conv_id]
    _show_positions()


def drain() -> int:
    """Start waiting turns, oldest first, while there is room. Returns how many started."""
    started: list[_Waiter] = []
    with _lock:
        while _waiting and _room():
            w = _waiting.pop(0)
            _admit(w.conv_id)
            started.append(w)
    for w in started:
        _say(w, "", position=0)
        threading.Thread(target=_run, args=(w,), daemon=True, name=f"admit-{w.conv_id[:8]}").start()
    _show_positions()
    _notices()
    return len(started)


def snapshot() -> dict[str, Any]:
    """For the tray / health: ``{running, waiting: [conv ids in order], free_gb}``."""
    with _lock:
        return {"running": len(_running), "waiting": [w.conv_id for w in _waiting], "free_gb": round(_read_free(), 2)}


def _run(w: _Waiter) -> None:
    try:
        w.start()  # run_message(..., _mem_admitted=True): it releases the slot if no turn starts
    except Exception:
        release(w.conv_id)


def _ensure_thread() -> None:
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(target=_loop, daemon=True, name="memory-admission")
        _thread.start()


def _loop() -> None:
    global _thread
    while True:
        _wake.wait(RECHECK_S)
        _wake.clear()
        drain()
        with _lock:
            if not _waiting:
                _thread = None
                return


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _say(w: _Waiter, text: str, *, position: int, top: list[dict[str, Any]] | None = None) -> None:
    push = w.push
    if push is None:
        return
    event: dict[str, Any] = {"type": "waiting_for_memory", "conv_id": w.conv_id, "position": position, "text": text}
    if top is not None:
        event["top"] = top
    try:
        push(event)
    except Exception:
        pass


def _show_positions() -> None:
    with _lock:
        moved = [(w, i + 1) for i, w in enumerate(_waiting) if w.shown != i + 1]
        for w, pos in moved:
            w.shown = pos
    if not moved:
        return
    free = _read_free()
    for w, pos in moved:
        _say(w, f"Waiting for memory ({free:.1f} GB free) — {_ordinal(pos)} in line", position=pos)


def biggest_users(n: int = 5) -> list[dict[str, Any]]:
    """The ``n`` process names holding the most private memory, summed over their processes."""
    from backend.util import proc_table

    by_name: dict[str, int] = {}
    for proc in proc_table.table().values():
        if proc.name:
            by_name[proc.name] = by_name.get(proc.name, 0) + int(proc.private)
    top = sorted(by_name.items(), key=lambda kv: kv[1], reverse=True)[:n]
    return [{"name": name, "gb": round(size / 1024**3, 1)} for name, size in top]


def _notices() -> None:
    now = _now()
    with _lock:
        due = [(w, i + 1) for i, w in enumerate(_waiting) if not w.noticed and now - w.since >= NOTICE_AFTER_S]
        for w, _ in due:
            w.noticed = True
    if not due:
        return
    try:
        top = biggest_users()
    except Exception:
        top = []
    names = ", ".join(f"{t['name']} {t['gb']:g} GB" for t in top)
    for w, pos in due:
        mins = int((now - w.since) // 60)
        _say(w, f"Waiting for memory for {mins} min — {_ordinal(pos)} in line. Biggest memory users: {names}",
             position=pos, top=top)
