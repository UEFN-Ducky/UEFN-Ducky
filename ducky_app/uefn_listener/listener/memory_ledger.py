"""UEFN's own memory, read from inside the editor (no psutil in UEFN).

``private_bytes()`` is the editor process's commit charge (PrivateUsage): what
counts against Windows' commit limit, so what runs UEFN out of memory.
"""

from __future__ import annotations

import ctypes
import os

MB = 1024 * 1024


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
