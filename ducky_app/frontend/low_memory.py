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

FREE_EVERY_S = 300.0  # Ducky frees its own leftovers at most this often, and once per low spell

_last_alert = 0.0  # the last critical alert
_last_freed = 0.0  # when Ducky last freed its own leftovers
_freed_this_spell = False  # reset when free memory is back above the warning level
_gpu_warned = False  # one graphics-memory alert per spell: reset when the GPU is under FULL_SHARE again
_last_warn = 0.0  # the last warning or critical alert
_recent: deque[tuple[float, float]] = deque()  # (monotonic, free GB) within DROP_WINDOW_S


def _gb(size: int) -> float:
    return round(size / 1024**3, 1)


def biggest(n: int = 3) -> list[dict[str, Any]]:
    """The ``n`` programs holding the most private memory: ``[{name, gb}]``, [] when the process
    list can't be read. Ducky and everything it started (WebView2, agent CLIs, the bridge) are
    one "UEFN Ducky + agents" row; other programs are summed by name without ".exe"."""
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
        return []
    rows = sorted(by_name.items(), key=lambda kv: kv[1], reverse=True)[:n]
    return [{"name": name, "gb": _gb(size)} for name, size in rows]


def _names(rows: list[dict[str, Any]]) -> str:
    return ", ".join(f"{r['name']} {float(r['gb']):g} GB" for r in rows)


def biggest_programs(n: int = 3) -> str:
    """'chrome 6.2 GB, UEFN Ducky + agents 1.9 GB, …', or '' when the process list can't be read."""
    return _names(biggest(n))


def _uefn_growth() -> dict[str, float] | None:
    try:
        from backend.bridge.editor_gateway import uefn_growth

        return uefn_growth()
    except Exception:
        return None


def uefn_growth_line(rows: list[dict[str, Any]]) -> str:
    """When UEFN is the biggest user and grew past ``uefn_memory_growth_limit_gb`` since its
    island loaded: the sentence saying so and the fix; '' otherwise."""
    if not rows or not str(rows[0].get("name") or "").lower().startswith("unrealeditor"):
        return ""
    growth = _uefn_growth() or {}
    grown, limit = float(growth.get("grown_gb") or 0), float(growth.get("limit_gb") or 0)
    if not limit or grown <= limit:
        return ""
    return f"UEFN grew {grown:.1f} GB since the island loaded: save and restart UEFN to get it back."


def ram_text(free: float, total: Any) -> str:
    """'Low RAM: 0.3 GB of 16 GB free' (the total left out when unknown)."""
    try:
        total_gb = float(total)
    except (TypeError, ValueError):
        total_gb = 0.0
    return f"Low RAM: {free:.1f} GB of {total_gb:.0f} GB free" if total_gb > 0 else f"Low RAM: {free:.1f} GB free"


def describe(free: float, total: Any, *, freed: dict[str, Any] | None = None, falling: bool = False,
             rows: list[dict[str, Any]] | None = None) -> str:
    """The real cause in one paragraph: 'Low RAM: 0.6 GB of 16 GB free. Ducky already freed 1.4 GB
    (…). Biggest users: UnrealEditorFortnite 10.8 GB, … UEFN grew 6.2 GB since the island loaded:
    save and restart UEFN to get it back.'"""
    rows = biggest() if rows is None else rows
    parts = [ram_text(free, total) + (" and falling fast." if falling else ".")]
    if freed:
        parts.append(_freed_text(freed).strip())
    if rows:
        parts.append(f"Biggest users: {_names(rows)}.")
    parts.append(uefn_growth_line(rows))
    return " ".join(p for p in parts if p)

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


def _free_own_leftovers(beat: dict[str, Any], now: float) -> dict[str, Any]:
    """Once per low spell (and at most every FREE_EVERY_S): Ducky stops its own leftovers."""
    global _last_freed, _freed_this_spell
    if _freed_this_spell or (_last_freed and now - _last_freed < FREE_EVERY_S):
        return {}
    _freed_this_spell, _last_freed = True, now
    try:
        from frontend import self_health

        return self_health.free_ducky_memory(beat) or {}
    except Exception:
        return {}


def _freed_text(freed: dict[str, Any]) -> str:
    """'Ducky already freed 1.4 GB (2 leftover bridges, 3 idle terminals). ', or '' when it freed nothing."""
    did = [str(d) for d in freed.get("did") or []]
    mb = float(freed.get("freed_mb") or 0)
    if not did and mb < 50:
        return ""
    amount = f" {mb / 1024:.1f} GB" if mb >= 50 else ""
    return f"Ducky already freed{amount}" + (f" ({', '.join(did)})" if did else "") + ". "


def check_gpu() -> None:
    """One alert per spell when the graphics card's memory is nearly full, naming what fills it."""
    global _gpu_warned
    try:
        from frontend import gpu_memory

        info = gpu_memory.read()
        if not gpu_memory.full(info):
            if info:
                _gpu_warned = False
            return
        if _gpu_warned:
            return
        _gpu_warned = True
        what, fix = gpu_memory.describe(info)
        from frontend import notifications

        notifications.alert("UEFN Ducky", "Graphics memory is almost full.", level="error",
                            detail=f"{what} {fix} Save your work in UEFN.")
    except Exception:
        pass


def on_heartbeat(beat: dict[str, Any]) -> None:
    """boot_report's 30 s sample: graphics memory first, then system RAM."""
    check_gpu()
    _check_ram(beat)


def _check_ram(beat: dict[str, Any]) -> None:
    """boot_report's 30 s memory sample: a warning when the PC is getting full or emptying fast,
    and a stronger alert when it is nearly full. Below the warning level Ducky first frees its
    own leftovers (once per spell) and only warns when that wasn't enough."""
    global _last_alert, _last_warn, _freed_this_spell
    free = beat.get("free_gb")
    if not isinstance(free, (int, float)) or isinstance(free, bool):
        return
    now = time.monotonic()
    dropped = _fast_drop(now, float(free))
    warn_at = warn_below_gb(beat.get("total_gb"))
    freed: dict[str, Any] = {}
    if free >= warn_at:
        _freed_this_spell = False
    else:
        freed = _free_own_leftovers(beat, now)
        if freed.get("did") or float(freed.get("freed_mb") or 0) > 0:
            try:
                from frontend.ui_web.admission import free_memory_gb

                free = free_memory_gb()
            except Exception:
                pass
            if free >= warn_at:
                return  # Ducky's own leftovers were enough: nothing to tell the person
    critical = free < RUN_GB
    if critical:
        if _last_alert and now - _last_alert < RUN_EVERY_S:
            return
    elif free < warn_at or dropped:
        if _last_warn and now - _last_warn < WARN_EVERY_S:
            return
    else:
        return
    _last_warn = now
    if critical:
        _last_alert = now
    cause = describe(float(free), beat.get("total_gb"), freed=freed, falling=dropped and not critical)
    from frontend import notifications

    if critical:
        notifications.alert(
            "UEFN Ducky", "Your PC is almost out of RAM.", level="error",
            detail=(f"{cause} Save your work in UEFN now, and close a program you "
                    "don't need, or Ducky may close by itself."),
        )
    else:
        notifications.alert(
            "UEFN Ducky", "Your PC is running low on RAM.", level="error",
            detail=(f"{cause} Save your work in UEFN, and close a program you "
                    "don't need before RAM runs out."),
        )
    _count_warning(float(free))
