"""Tell the person when their PC is nearly out of memory, before Ducky dies of it.

Oct 30 2026: a 16 GB PC with 0.5 GB free closed Ducky while WebView2 started, before any window
showed, and the next launch closed by itself 3 minutes in at 0.8 GB free. Ducky was 0.6 GB of
that. The crash reports said so; the person never learned that the PC itself was full.

At launch, below LAUNCH_GB free, a Windows message names the programs using the most memory and
waits for Try Again / Continue / Cancel. While Ducky runs, below RUN_GB an alert names them, at
most every RUN_EVERY_S.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Callable

LAUNCH_GB = 1.0
RUN_GB = 0.7
RUN_EVERY_S = 1800.0
# Windows' own memory accounting, not programs the person can close.
_NOT_PROGRAMS = {"system", "registry", "memory compression", "secure system", "idle"}

_last_alert = 0.0


def biggest_programs(n: int = 3) -> str:
    """'chrome 6.2 GB, UnrealEditorFortnite 5.1 GB, …', or '' when the process list can't be read."""
    try:
        from frontend.ui_web.admission import biggest_users

        rows = [r for r in biggest_users(n + len(_NOT_PROGRAMS)) if r["name"].lower() not in _NOT_PROGRAMS][:n]
    except Exception:
        return ""
    return ", ".join(f"{re.sub(r'(?i)[.]exe$', '', r['name'])} {r['gb']:g} GB" for r in rows)


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


def on_heartbeat(beat: dict[str, Any]) -> None:
    """boot_report's 30 s memory sample: an alert when the PC itself is nearly full."""
    global _last_alert
    free = beat.get("free_gb")
    if not isinstance(free, (int, float)) or free >= RUN_GB:
        return
    now = time.monotonic()
    if _last_alert and now - _last_alert < RUN_EVERY_S:
        return
    _last_alert = now
    names = biggest_programs()
    from frontend import notifications

    notifications.alert(
        "UEFN Ducky", "Your PC is almost out of memory.", level="error",
        detail=(f"{free:.1f} GB free." + (f" Using the most: {names}." if names else "")
                + " Close a program you don't need, or Ducky may close by itself."),
    )
