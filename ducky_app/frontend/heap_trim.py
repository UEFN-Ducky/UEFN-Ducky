"""Give freed heap back to Windows after a big free.

After a peak (a big chat load, a large plugin reply) the Windows process heap keeps the freed
memory committed: Nov 5 2026 the app held 1,043 MB of heap with 291 MB in use, and Task Manager
counted all of it. ``trim()`` runs a gc pass, then ``HeapCompact`` on every process heap and the
CRT's ``_heapmin()``, which decommit the free ranges.

``maybe_trim()`` is the idle-tick entry: at most every EVERY_S, only when the heaps hold more
than FREE_OVER_MB committed but unused, and on its own background thread (HeapCompact takes
10-100 ms and the gc pass holds the GIL), never the caller's. Off Windows everything is a no-op.
"""

from __future__ import annotations

import gc
import sys
import threading
import time
from typing import Any

EVERY_S = 300.0
FREE_OVER_MB = 200.0
MB = 1024 * 1024

_lock = threading.Lock()  # one trim at a time
_last = 0.0  # monotonic time of the last trim started by maybe_trim
_running = False


def _k32() -> Any:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetProcessHeaps.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    k32.GetProcessHeaps.restype = wintypes.DWORD
    k32.HeapCompact.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.HeapCompact.restype = ctypes.c_size_t
    return k32


def _heaps(k32: Any) -> list[Any]:
    import ctypes
    from ctypes import wintypes

    n = int(k32.GetProcessHeaps(0, None) or 0)
    if n <= 0:
        return []
    arr = (wintypes.HANDLE * n)()
    n = int(k32.GetProcessHeaps(n, arr) or 0)
    return list(arr[:n])


def heap_free_mb() -> float:
    """Committed minus allocated over every process heap, in MB (0.0 off Windows or on error)."""
    if sys.platform != "win32":
        return 0.0
    try:
        import ctypes
        from ctypes import wintypes

        class HeapSummary(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("cbAllocated", ctypes.c_size_t), ("cbCommitted", ctypes.c_size_t),
                        ("cbReserved", ctypes.c_size_t), ("cbMaxReserve", ctypes.c_size_t)]

        k32 = _k32()
        k32.HeapSummary.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(HeapSummary)]
        free = 0
        for h in _heaps(k32):
            s = HeapSummary()
            s.cb = ctypes.sizeof(s)
            if k32.HeapSummary(h, 0, ctypes.byref(s)):
                free += max(int(s.cbCommitted) - int(s.cbAllocated), 0)
        return round(free / MB, 1)
    except Exception:
        return 0.0


def _private_bytes() -> int:
    try:
        import psutil

        mem = psutil.Process().memory_info()
        return int(getattr(mem, "private", 0) or mem.rss)
    except Exception:
        return 0


def _optimize_resources(k32: Any) -> None:
    """HeapSetInformation(NULL, HeapOptimizeResources): decommit free pages from every heap,
    the low-fragmentation buckets included (Windows 8.1+). HeapCompact alone never touches
    those, and they hold most of what a peak of small Python objects leaves behind."""
    import ctypes
    from ctypes import wintypes

    class Info(ctypes.Structure):
        _fields_ = [("Version", wintypes.DWORD), ("Flags", wintypes.DWORD)]

    info = Info(1, 0)
    k32.HeapSetInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t]
    k32.HeapSetInformation.restype = wintypes.BOOL
    k32.HeapSetInformation(None, 3, ctypes.byref(info), ctypes.sizeof(info))


def _compact() -> None:
    k32 = _k32()
    try:
        _optimize_resources(k32)
    except Exception:
        pass
    for h in _heaps(k32):
        try:
            k32.HeapCompact(h, 0)
        except Exception:
            pass
    try:
        import ctypes

        ctypes.CDLL("ucrtbase")._heapmin()
    except Exception:
        pass


def trim() -> dict[str, Any]:
    """gc, then give the heaps' free ranges back: ``{"freed_mb", "took_ms"}`` (private bytes
    before minus after). Off Windows: ``{"freed_mb": 0.0, "took_ms": 0.0, "skipped": "not win32"}``."""
    if sys.platform != "win32":
        return {"freed_mb": 0.0, "took_ms": 0.0, "skipped": "not win32"}
    with _lock:
        t0 = time.perf_counter()
        before = _private_bytes()
        gc.collect()
        try:
            _compact()
        except Exception as exc:  # noqa: BLE001 — a failed trim must never hurt the caller
            return {"freed_mb": 0.0, "took_ms": round((time.perf_counter() - t0) * 1000, 1), "error": str(exc)[:200]}
        after = _private_bytes()
        return {"freed_mb": round(max(before - after, 0) / MB, 1),
                "took_ms": round((time.perf_counter() - t0) * 1000, 1)}


def _run() -> None:
    global _running
    try:
        trim()
    finally:
        _running = False


def maybe_trim(*, now: float | None = None) -> bool:
    """Idle tick: start a background trim when due and worth it. True when one was started."""
    global _last, _running
    if sys.platform != "win32" or _running:
        return False
    now = time.monotonic() if now is None else now
    if _last and now - _last < EVERY_S:
        return False
    if heap_free_mb() < FREE_OVER_MB:
        return False
    _last, _running = now, True
    try:
        threading.Thread(target=_run, name="heap-trim", daemon=True).start()
    except Exception:
        _running = False
        return False
    return True
