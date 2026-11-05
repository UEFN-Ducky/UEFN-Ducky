"""Which listener command grows UEFN: a per-command memory ledger.

``private_bytes()`` is the editor process's commit charge (PrivateUsage): what
counts against Windows' commit limit, so what runs UEFN out of memory. No psutil
in UEFN, so it is read with ctypes.

The tick reads it before and after each command (``record``). Growth that is
given back within ``KEEP_AFTER_SEC`` is a spike, not a leak: each growth is
re-checked once it is due (``settle``) and only what is still held, capped at
the original growth, counts as ``kept_mb``. One row per command name, kept in
the listener's metrics dict (``unreal._mcp_metrics``, which survives listener
reloads) and reset when the UEFN process changes.
"""

from __future__ import annotations

import ctypes
import os
import time

MB = 1024 * 1024
KEEP_AFTER_SEC = 60.0
TOP_N = 10
#: Pending re-checks kept at most (a burst of 1,000 captures must not grow the list).
MAX_PENDING = 256
_KEY = "memory_ledger"


class _ProcessMemoryCountersEx(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


_reader = None


def _make_reader():
    if os.name != "nt":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    fn = getattr(kernel32, "K32GetProcessMemoryInfo", None)
    if fn is None:
        fn = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
    fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ProcessMemoryCountersEx), ctypes.c_ulong]
    fn.restype = ctypes.c_int
    handle = kernel32.GetCurrentProcess()

    def read() -> int:
        counters = _ProcessMemoryCountersEx()
        counters.cb = ctypes.sizeof(counters)
        if not fn(handle, ctypes.byref(counters), counters.cb):
            return 0
        return int(counters.PrivateUsage)

    return read


def private_bytes() -> int:
    """This process's private (committed) bytes; 0 when it can't be read."""
    global _reader
    try:
        if _reader is None:
            _reader = _make_reader() or (lambda: 0)
        return int(_reader())
    except Exception:
        return 0


# Seams (tests replace these).
clock = time.time
read_bytes = private_bytes
current_pid = os.getpid


def _ledger(store: dict) -> dict:
    pid = current_pid()
    led = store.get(_KEY)
    if not isinstance(led, dict) or led.get("pid") != pid:
        led = {"pid": pid, "rows": {}, "pending": []}
        store[_KEY] = led
    return led


def record(store: dict, command: str, before: int, after: int) -> None:
    """Count one call of ``command`` that took UEFN from ``before`` to ``after`` bytes."""
    led = _ledger(store)
    row = led["rows"].get(command)
    if row is None:
        row = led["rows"][command] = {"calls": 0, "grew_mb": 0.0, "kept_mb": 0.0}
    row["calls"] += 1
    if before <= 0 or after <= 0:
        return  # memory unreadable: count the call only
    grew = after - before
    if grew <= 0:
        return
    row["grew_mb"] += grew / MB
    pending = led["pending"]
    pending.append((command, int(before), int(grew), clock() + KEEP_AFTER_SEC))
    if len(pending) > MAX_PENDING:
        del pending[: len(pending) - MAX_PENDING]


def settle(store: dict) -> None:
    """Credit ``kept_mb`` for growths that are due. Cheap when nothing is due."""
    led = store.get(_KEY)
    if not isinstance(led, dict):
        return
    pending = led.get("pending") or []
    if not pending:
        return
    now = clock()
    if pending[0][3] > now:
        return
    led = _ledger(store)
    pending = led["pending"]
    now_bytes = read_bytes()
    keep = []
    for item in pending:
        command, before, grew, due = item
        if due > now:
            keep.append(item)
            continue
        if now_bytes <= 0:
            continue
        kept = min(grew, max(0, now_bytes - before))
        row = led["rows"].get(command)
        if row is not None:
            row["kept_mb"] += kept / MB
    led["pending"] = keep


def snapshot(store: dict, top: int = TOP_N) -> dict:
    """``{uefn_mb_now, rows}``: the top commands by memory still held."""
    led = _ledger(store)
    rows = [
        {
            "command": name,
            "calls": r["calls"],
            "grew_mb": round(r["grew_mb"], 1),
            "kept_mb": round(r["kept_mb"], 1),
        }
        for name, r in led["rows"].items()
    ]
    rows.sort(key=lambda r: (r["kept_mb"], r["grew_mb"]), reverse=True)
    now_bytes = read_bytes()
    return {
        "uefn_mb_now": round(now_bytes / MB) if now_bytes > 0 else None,
        "rows": rows[: max(0, int(top))],
    }
