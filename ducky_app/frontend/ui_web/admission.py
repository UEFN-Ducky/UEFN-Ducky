"""Team member turns and memory: every turn starts at once; low memory only warns.

Agents once kept starting while the PC had 0.1 GB free (decision D2), so member turns used to
wait in a FIFO line for memory ("Waiting for memory (N GB free) — 2nd in line"). Nov 5 2026 the
person: agents never wait on others. A sub-lead's or member's turn now always starts. When free
RAM is under ``agent_min_free_ram_gb`` (default 15% of the PC's RAM, 1.5 to 6 GB; env
``UEFN_DUCKY_AGENT_MIN_FREE_RAM_GB`` wins), one warning naming the biggest memory users is
posted; the next one only after memory recovered above the floor and dropped again.
An admitted member turn still holds a slot until it ends (``release``): the seat queue counts
running member turns from it.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable

# Default for the agent_min_free_ram_gb setting: a share of the PC's RAM, kept between these.
START_FREE_GB = 6.0
MIN_START_FREE_GB = 1.5
START_FREE_SHARE = 0.15
FLOOR_EVERY_S = 10.0
_ROLE_TTL_S = 10.0

PushFn = Callable[[dict[str, Any]], None]

_lock = threading.Lock()
_running: set[str] = set()  # member / sub-lead turns holding a slot
_roles: dict[str, tuple[float, str]] = {}
_now = time.monotonic
_floor_cache: dict[str, float] = {"gb": START_FREE_GB, "at": float("-inf")}
_warned = {"low": False}  # a low-memory warning went out and memory has not recovered since


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
        return float("inf")  # can't tell: never warn on a guess


_read_free = free_memory_gb


def default_min_free_gb(total_gb: float | None = None) -> float:
    """15% of the PC's RAM, at least 1.5 GB and at most 6 GB (8 GB -> 1.5, 16 -> 2.4, 32 -> 4.8)."""
    if total_gb is None:
        from frontend.ui_web.seat_queue import total_memory_gb

        total_gb = total_memory_gb()
    return round(min(START_FREE_GB, max(MIN_START_FREE_GB, START_FREE_SHARE * float(total_gb))), 2)


def min_free_gb() -> float:
    """The low-memory warning line: the ``agent_min_free_ram_gb`` setting (env
    ``UEFN_DUCKY_AGENT_MIN_FREE_RAM_GB`` wins), else ``default_min_free_gb``."""
    now = _now()
    if now - _floor_cache["at"] < FLOOR_EVERY_S:
        return _floor_cache["gb"]
    gb = default_min_free_gb()
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


def _counts(conv_id: str, source: str) -> bool:
    """A member's or sub-lead's agent-started turn takes a slot; a person's send, Stop, an
    answer, a top lead and a solo chat take none."""
    return source not in ("person", "stop", "answer") and _role(conv_id) in ("sub_lead", "member")


def may_start(conv_id: str, source: str, *, start: Callable[[], Any] | None = None,
              push: PushFn | None = None) -> bool:
    """Always True: the turn starts now. A member turn takes a slot (``release`` frees it), and
    under the memory floor one warning is posted to ``push``. ``start`` is unused (kept for
    callers that still pass it)."""
    if not _counts(conv_id, source):
        return True
    with _lock:
        _running.add(conv_id)
    _warn_if_low(conv_id, push)
    return True


def holds(conv_id: str) -> bool:
    """True when the chat holds a member slot (it must ``release`` it when its turn ends)."""
    with _lock:
        return conv_id in _running


def release(conv_id: str) -> None:
    """The chat's turn ended (or never started): free its slot."""
    with _lock:
        _running.discard(conv_id)


def snapshot() -> dict[str, Any]:
    """For the tray / health: ``{running, waiting: [], free_gb}`` (nothing ever waits)."""
    with _lock:
        running = len(_running)
    return {"running": running, "waiting": [], "free_gb": round(_read_free(), 2)}


def biggest_users(n: int = 5) -> list[dict[str, Any]]:
    """The ``n`` programs holding the most private memory (``[{name, gb}]``): Ducky's whole
    process tree is one "UEFN Ducky + agents" row, other programs drop ".exe"."""
    from frontend.low_memory import biggest

    return biggest(n)


def _total_gb() -> float:
    from frontend.ui_web.seat_queue import total_memory_gb

    return float(total_memory_gb())


def _warn_if_low(conv_id: str, push: PushFn | None) -> None:
    """One warning per low-memory spell; never holds the turn."""
    if push is None:
        return
    try:
        free = _read_free()
        floor = min_free_gb()
    except Exception:
        return
    with _lock:
        if free >= floor:
            _warned["low"] = False
            return
        if _warned["low"]:
            return
        _warned["low"] = True
    try:
        top = biggest_users()
    except Exception:
        top = []
    from frontend.low_memory import describe

    try:
        total = _total_gb()
    except Exception:
        total = 0.0
    text = f"{describe(free, total, rows=top)} Agent turns still start (warning line {floor:g} GB)."
    try:
        push({"type": "low_memory_warning", "conv_id": conv_id, "text": text, "free_gb": round(free, 2),
              "top": top})
    except Exception:
        pass
