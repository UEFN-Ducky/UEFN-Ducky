"""How much of the PC agents' terminals and sandbox programs may take, together.

Nov 3 2026: an agent's ``cargo test`` compiled a 220k-line crate; one rustc held 6.4 GB beside
UEFN (12 GB), nine agents and 26 node test workers. On a 16 GB PC that freezes everything.
Agent terminals now run below normal priority inside a memory cap (the person's own terminals
keep neither). Test and build runs never wait on each other's (Nov 5 2026).

Nov 5 2026: UEFN died with "The paging file is too small" (Windows' commit limit) while each
agent terminal had its own 30% cap and nothing counted UEFN. Every agent terminal and sandbox
program now shares ONE budget: the commit limit, less what everything else holds, a reserve,
and room for UEFN to grow. A ticker (TerminalManager) splits it across the live jobs every few
seconds and lowers or raises their caps while they run. Nothing waits for memory.
"""

from __future__ import annotations

import ctypes
import os
import re
import threading
from typing import Any, Iterable

CAP_SHARE = 0.30  # of the PC's memory: one job's cap when the budget can't be measured
CAP_FLOOR_MB = 4096
SHARE_FLOOR_MB = 1024  # every job gets at least this, whatever the budget says
RESERVE_FLOOR_MB = 4096
RESERVE_SHARE = 0.10  # of the PC's RAM, kept free of agent runs
UEFN_HEADROOM_FLOOR_MB = 4096
UEFN_HEADROOM_SHARE = 0.25  # of UEFN's private memory, kept for it to grow into
_NEAR_CAP = 0.95
_UEFN_PREFIX = "unrealeditorfortnite"
# Commands that run many workers at once; with UEFN open they run at idle priority.
_HEAVY_RE = re.compile(r"\b(pytest|vitest|build_exes|cargo|npm\s+run\s+build|pnpm\s+(run\s+)?build|yarn\s+build)\b",
                       re.IGNORECASE)

_last_lock = threading.Lock()
# The last split: budget_mb, share_mb, uefn_mb, jobs (cap_hint and new jobs read it).
_last: dict[str, int] = {}


class _Status(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + [
        (name, ctypes.c_uint64) for name in ("total", "avail", "page_total", "page_avail",
                                              "virt_total", "virt_avail", "ext_avail")
    ]


def _memory_status() -> _Status | None:
    if os.name != "nt":
        return None
    status = _Status()
    status.dwLength = ctypes.sizeof(status)
    try:
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status
    except Exception:
        pass
    return None


def total_memory_mb() -> int:
    """The PC's physical memory in MB (16 GB when Windows can't say)."""
    if os.name != "nt":
        try:
            return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1024 * 1024))
        except (ValueError, OSError, AttributeError):
            return 16384
    status = _memory_status()
    return int(status.total // (1024 * 1024)) if status is not None else 16384


def commit_mb() -> tuple[int, int]:
    """(Windows' commit limit = RAM + page file, commit in use), in MB; (0, 0) when unknown."""
    status = _memory_status()
    if status is None:
        return 0, 0
    limit = int(status.page_total // (1024 * 1024))
    return limit, limit - int(status.page_avail // (1024 * 1024))


def private_mb(pids: Iterable[int]) -> int:
    """The committed (private) memory the processes hold together, in MB."""
    if os.name != "nt":
        return 0
    from ctypes import wintypes

    class _Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
                "PrivateUsage")
        ]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.K32GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD)
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    total = 0
    for pid in set(int(p) for p in pids if p):
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            continue
        try:
            counters = _Counters()
            counters.cb = ctypes.sizeof(counters)
            if k32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                total += int(counters.PrivateUsage)
        finally:
            k32.CloseHandle(handle)
    return total // (1024 * 1024)


def uefn_pids(names: dict[int, str]) -> list[int]:
    return [pid for pid, name in names.items() if (name or "").lower().startswith(_UEFN_PREFIX)]


def uefn_private_mb(names: dict[int, str] | None = None) -> int:
    """UEFN's committed memory in MB; 0 when UEFN is not open."""
    if names is None:
        if os.name != "nt":
            return 0
        from frontend.ui_web.terminal.session import _process_snapshot

        _children, names = _process_snapshot()
    pids = uefn_pids(names)
    return max(1, private_mb(pids)) if pids else 0


def uefn_headroom_mb(uefn_mb: int) -> int:
    return max(UEFN_HEADROOM_FLOOR_MB, int(uefn_mb * UEFN_HEADROOM_SHARE)) if uefn_mb > 0 else 0


def budget_mb(*, commit_limit_mb: int, commit_used_mb: int, agent_used_mb: int, ram_mb: int,
              uefn_mb: int = 0) -> int:
    """What every agent terminal and sandbox program may commit together: the commit limit,
    less what everything else commits, a reserve (max(4 GB, 10% of RAM)) and, with UEFN open,
    room for it to grow (max(4 GB, 25% of its private memory)). Never below 0."""
    outside = max(0, int(commit_used_mb) - int(agent_used_mb))
    reserve = max(RESERVE_FLOOR_MB, int(ram_mb * RESERVE_SHARE))
    return max(0, int(commit_limit_mb) - outside - reserve - uefn_headroom_mb(int(uefn_mb)))


def split_mb(budget: int, jobs: int) -> int:
    """One job's share: an equal split, at least SHARE_FLOOR_MB."""
    return max(SHARE_FLOOR_MB, int(budget) // max(1, int(jobs)))


def measure(jobs: list[Any] | None = None) -> dict[str, int]:
    """The live numbers: budget_mb, uefn_mb, jobs. Empty when Windows can't say."""
    from backend.sandbox import jobs as job_mod

    live = job_mod.shared_jobs() if jobs is None else jobs
    limit, used = commit_mb()
    if not limit:
        return {}
    agent_pids = [pid for job in live for pid in job.pids()]
    uefn = uefn_private_mb()
    budget = budget_mb(commit_limit_mb=limit, commit_used_mb=used, agent_used_mb=private_mb(agent_pids),
                       ram_mb=total_memory_mb(), uefn_mb=uefn)
    return {"budget_mb": budget, "uefn_mb": uefn, "jobs": len(live)}


def rebalance(jobs: list[Any] | None = None, numbers: dict[str, int] | None = None) -> dict[str, int]:
    """Split the budget across the live shared jobs and set each one's cap (a job's own
    ceiling_mb, when set, stays its most). Returns the split; {} when nothing was measured."""
    from backend.sandbox import jobs as job_mod

    live = job_mod.shared_jobs() if jobs is None else list(jobs)
    numbers = measure(live) if numbers is None else dict(numbers)
    if not numbers:
        return {}
    share = split_mb(numbers["budget_mb"], len(live))
    for job in live:
        ceiling = int(getattr(job, "ceiling_mb", 0) or 0)
        cap = min(share, ceiling) if ceiling else share
        if int(getattr(job, "memory_mb", 0) or 0) != cap:
            try:
                job.set_memory_limit(cap)
            except Exception:
                pass
    out = {**numbers, "share_mb": share, "jobs": len(live)}
    with _last_lock:
        _last.clear()
        _last.update(out)
    return out


def new_job_cap_mb() -> int:
    """The cap a job starting now gets: its share with one more job in the split."""
    try:
        from backend.sandbox import jobs as job_mod

        live = job_mod.shared_jobs()
        numbers = measure(live)
    except Exception:
        numbers, live = {}, []
    if not numbers:
        return memory_cap_mb()
    return split_mb(numbers["budget_mb"], len(live) + 1)


def last_split() -> dict[str, int]:
    with _last_lock:
        return dict(_last)


def is_heavy(command: str) -> bool:
    """A test or build run (many workers at once)."""
    return bool(_HEAVY_RE.search(command or ""))


def memory_cap_mb(total_mb: int | None = None) -> int:
    """One job's cap when the budget can't be measured: CAP_SHARE of the PC, at least
    CAP_FLOOR_MB (never more than the PC has)."""
    total = int(total_mb or total_memory_mb())
    return min(total, max(CAP_FLOOR_MB, int(total * CAP_SHARE)))


def hit_cap(peak_before_mb: int, peak_after_mb: int, cap_mb: int) -> bool:
    """The terminal's peak memory rose to the cap during this command (Windows then refused
    its allocations, so the command failed or was cut short)."""
    return cap_mb > 0 and peak_after_mb > peak_before_mb and peak_after_mb >= cap_mb * _NEAR_CAP


def cap_hint(peak_mb: int, cap_mb: int, total_mb: int | None = None, split: dict[str, int] | None = None) -> str:
    total = int(total_mb or total_memory_mb())
    split = last_split() if split is None else split
    shared = ""
    if split.get("budget_mb"):
        shared = (f" Agent runs share {split['budget_mb'] / 1024:.1f} GB across {split.get('jobs', 1)} "
                  "terminal(s) and sandbox program(s)")
        uefn = int(split.get("uefn_mb", 0) or 0)
        shared += (f", leaving room for UEFN ({uefn / 1024:.1f} GB)." if uefn else ".")
    return (
        f"This command reached Ducky's memory cap for an agent terminal ({peak_mb / 1024:.1f} GB of "
        f"{cap_mb / 1024:.1f} GB; the PC has {total / 1024:.0f} GB), so Windows refused it more memory.{shared} "
        "Do not retry it as is: run less at once (cargo -j 2, pytest -n 2 or without -n, vitest --maxWorkers=2), "
        "or test a smaller part (one crate, one test file)."
    )
