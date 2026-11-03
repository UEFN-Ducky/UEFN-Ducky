"""How much of the PC one agent's terminal may take.

Nov 3 2026: an agent's ``cargo test`` compiled a 220k-line crate; one rustc held 6.4 GB beside
UEFN (12 GB), nine agents and 26 node test workers. On a 16 GB PC that freezes everything.
Agent terminals now run below normal priority inside a memory cap (the person's own terminals
keep neither), and test and build runs take turns across the whole PC (repo_lock.PC).
"""

from __future__ import annotations

import ctypes
import os

CAP_SHARE = 0.30  # of the PC's memory, for everything one agent terminal runs at once
CAP_FLOOR_MB = 4096
_NEAR_CAP = 0.95


def total_memory_mb() -> int:
    """The PC's physical memory in MB (16 GB when Windows can't say)."""
    if os.name != "nt":
        try:
            return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1024 * 1024))
        except (ValueError, OSError, AttributeError):
            return 16384

    class _Status(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + [
            (name, ctypes.c_uint64) for name in ("total", "avail", "page_total", "page_avail",
                                                  "virt_total", "virt_avail", "ext_avail")
        ]

    status = _Status()
    status.dwLength = ctypes.sizeof(status)
    try:
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.total // (1024 * 1024))
    except Exception:
        pass
    return 16384


def memory_cap_mb(total_mb: int | None = None) -> int:
    """The memory cap for one agent terminal: CAP_SHARE of the PC, at least CAP_FLOOR_MB
    (never more than the PC has)."""
    total = int(total_mb or total_memory_mb())
    return min(total, max(CAP_FLOOR_MB, int(total * CAP_SHARE)))


def hit_cap(peak_before_mb: int, peak_after_mb: int, cap_mb: int) -> bool:
    """The terminal's peak memory rose to the cap during this command (Windows then refused
    its allocations, so the command failed or was cut short)."""
    return cap_mb > 0 and peak_after_mb > peak_before_mb and peak_after_mb >= cap_mb * _NEAR_CAP


def cap_hint(peak_mb: int, cap_mb: int, total_mb: int | None = None) -> str:
    total = int(total_mb or total_memory_mb())
    return (
        f"This command reached Ducky's memory cap for an agent terminal ({peak_mb / 1024:.1f} GB of "
        f"{cap_mb / 1024:.1f} GB; the PC has {total / 1024:.0f} GB), so Windows refused it more memory. "
        "Do not retry it as is: run less at once (cargo -j 2, pytest without -n, vitest --maxWorkers=2), "
        "or test a smaller part (one crate, one test file)."
    )
