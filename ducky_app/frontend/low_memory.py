"""Tell the person when their PC is nearly out of memory, before Ducky dies of it.

Oct 30 2026: a 16 GB PC with 0.5 GB free closed Ducky while WebView2 started, before any window
showed, and the next launch closed by itself 3 minutes in at 0.8 GB free. Ducky was 0.6 GB of
that. The crash reports said so; the person never learned that the PC itself was full.
Nov 4 2026: PCs fell from about 4 GB to 0.8-1.1 GB free and Ducky closed by itself with no
warning, because the only alert waited for 0.7 GB.

At launch, below LAUNCH_GB free, a Windows message names the programs using the most memory and
waits for Try Again / Continue / Cancel. While Ducky runs, an alert names them and says to save
in UEFN:
- a warning below WARN_GB free or WARN_SHARE of the PC's memory (whichever is higher), or when
  free memory fell by more than DROP_GB within DROP_WINDOW_S and is under DROP_UNDER_GB, at
  most every WARN_EVERY_S;
- a stronger alert below RUN_GB (critical), which never waits for the warning's quiet time and
  repeats at most every RUN_EVERY_S.
"""

from __future__ import annotations

import os
import re
import time
from collections import deque
from typing import Any, Callable

LAUNCH_GB = 1.0
RUN_GB = 0.7  # critical: the stronger alert
RUN_EVERY_S = 1800.0
WARN_GB = 1.5
WARN_SHARE = 0.10  # of the PC's total memory
WARN_EVERY_S = 600.0
DROP_GB = 2.0
DROP_WINDOW_S = 60.0
DROP_UNDER_GB = 4.0
DUCKY_LABEL = "UEFN Ducky + agents"
# Windows' own memory accounting, not programs the person can close.
_NOT_PROGRAMS = {"system", "registry", "memory compression", "secure system", "idle", "system idle process"}
# The usage event's only detail: how much was free, never which programs.
_FREE_BUCKETS = ((0.5, "under_0_5gb"), (1.0, "under_1gb"), (1.5, "under_1_5gb"), (2.0, "under_2gb"),
                 (4.0, "under_4gb"), (None, "4gb_plus"))

_last_alert = 0.0  # the last critical alert
_last_warn = 0.0  # the last warning or critical alert
_recent: deque[tuple[float, float]] = deque()  # (monotonic, free GB) within DROP_WINDOW_S


def _gb(size: int) -> float:
    return round(size / 1024**3, 1)


def biggest_programs(n: int = 3) -> str:
    """'chrome 6.2 GB, UEFN Ducky + agents 1.9 GB, …', or '' when the process list can't be read.

    Ducky and everything it started (WebView2, agent CLIs, the bridge) count as one program."""
    try:
        from backend.util import proc_table

        procs = proc_table.table()
        mine = set(proc_table.tree(procs, os.getpid()))
        by_name: dict[str, int] = {}
        ducky = 0
        for pid, proc in procs.items():
            if pid in mine:
                ducky += int(proc.private)
            elif proc.name and proc.name.lower() not in _NOT_PROGRAMS:
                name = re.sub(r"(?i)[.]exe$", "", proc.name)
                by_name[name] = by_name.get(name, 0) + int(proc.private)
        if ducky:
            by_name[DUCKY_LABEL] = by_name.get(DUCKY_LABEL, 0) + ducky
    except Exception:
        return ""
    rows = sorted(by_name.items(), key=lambda kv: kv[1], reverse=True)[:n]
    return ", ".join(f"{name} {_gb(size):g} GB" for name, size in rows)

def _message_box(free_gb: float, names: str) -> str:
    import ctypes

    text = (f"Your PC has only {free_gb:.1f} GB of memory free, so UEFN Ducky may close while it starts.\n\n"
            + (f"Using the most memory: {names}.\n\n" if names else "")
            + "Close a program you don't need, then choose Try Again. Continue starts UEFN Ducky anyway.")
    flags = 0x6 | 0x30 | 0x10000 | 0x40000  # Cancel / Try Again / Continue, warning icon, in front
    answer = ctypes.windll.user32.MessageBoxW(None, text, "UEFN Ducky: PC almost out of memory", flags)
    return {2: "cancel", 10: "try", 11: "continue"}.get(int(answer), "continue")


def check_at_launch(ask: Callable[[float, str], str] | None = None) -> bool:
    """Before the window starts. False when the person chose Cancel: don't start."""
    if os.name != "nt" or os.environ.get("DUCKY_SKIP_LOW_MEMORY_CHECK"):
        return True
    from frontend.ui_web.admission import free_memory_gb

    ask = ask or _message_box
    free = free_memory_gb()
    if free >= LAUNCH_GB:
        return True
    _count()
    while free < LAUNCH_GB:
        answer = ask(free, biggest_programs())
        if answer == "cancel":
            return False
        if answer == "continue":
            break
        free = free_memory_gb()
    return True


def _count() -> None:
    try:
        from frontend import usage_stats

        usage_stats.error("low_memory_start", "out of memory", at="launch", kind="memory")
    except Exception:
        pass


def warn_below_gb(total_gb: Any) -> float:
    """The warning level: WARN_GB, or WARN_SHARE of the PC's memory when that is higher."""
    try:
        share = float(total_gb) * WARN_SHARE
    except (TypeError, ValueError):
        share = 0.0
    return max(WARN_GB, share)


def _fast_drop(now: float, free: float) -> bool:
    """True when free memory fell by more than DROP_GB within DROP_WINDOW_S and is under DROP_UNDER_GB."""
    while _recent and now - _recent[0][0] > DROP_WINDOW_S:
        _recent.popleft()
    peak = max((f for _, f in _recent), default=free)
    _recent.append((now, free))
    return free < DROP_UNDER_GB and peak - free > DROP_GB


def free_bucket(free: float) -> str:
    for bound, label in _FREE_BUCKETS:
        if bound is None or free < bound:
            return label
    return _FREE_BUCKETS[-1][1]


def _count_warning(free: float) -> None:
    try:
        from frontend import usage_stats

        usage_stats.track("low_memory.warned", free=free_bucket(free))
    except Exception:
        pass


def on_heartbeat(beat: dict[str, Any]) -> None:
    """boot_report's 30 s memory sample: a warning when the PC is getting full or emptying fast,
    and a stronger alert when it is nearly full."""
    global _last_alert, _last_warn
    free = beat.get("free_gb")
    if not isinstance(free, (int, float)) or isinstance(free, bool):
        return
    now = time.monotonic()
    dropped = _fast_drop(now, float(free))
    critical = free < RUN_GB
    if critical:
        if _last_alert and now - _last_alert < RUN_EVERY_S:
            return
    elif free < warn_below_gb(beat.get("total_gb")) or dropped:
        if _last_warn and now - _last_warn < WARN_EVERY_S:
            return
    else:
        return
    _last_warn = now
    if critical:
        _last_alert = now
    names = biggest_programs()
    using = f" Using the most: {names}." if names else ""
    from frontend import notifications

    if critical:
        notifications.alert(
            "UEFN Ducky", "Your PC is almost out of memory.", level="error",
            detail=(f"{free:.1f} GB free.{using} Save your work in UEFN now, and close a program you "
                    "don't need, or Ducky may close by itself."),
        )
    else:
        why = " and falling fast" if dropped else ""
        notifications.alert(
            "UEFN Ducky", "Your PC is running low on memory.", level="error",
            detail=(f"{free:.1f} GB free{why}.{using} Save your work in UEFN, and close a program you "
                    "don't need before memory runs out."),
        )
    _count_warning(float(free))
