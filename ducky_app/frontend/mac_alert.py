"""macOS message boxes without Tk (Cocoa owns the main thread): ``osascript display alert``.

An in-process NSAlert ``runModal`` never returns on its own, so an unattended start waited
on it forever; ``giving up after`` closes the alert by itself. Any thread can call it."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence

# Text and buttons travel as argv, never spliced into the script (quotes, backslashes).
_SCRIPT = (
    "on run argv\n"
    "set r to display alert (item 1 of argv) message (item 2 of argv) as critical "
    "buttons (items 4 thru -1 of argv) default button 1 giving up after ((item 3 of argv) as integer)\n"
    "if gave up of r then return \"\"\n"
    "return button returned of r\n"
    "end run"
)


def ask(title: str, message: str, buttons: Sequence[str] = ("OK",), *, wait_s: float = 600.0) -> int:
    """Show an alert; the index of the button pressed, or -1 (closed itself after *wait_s*,
    or no osascript). At most 3 buttons (macOS's limit)."""
    labels = [str(b) for b in buttons][:3] or ["OK"]
    wait = max(1, int(wait_s))
    try:
        out = subprocess.run(
            ["osascript", "-e", _SCRIPT, title, message, str(wait), *labels],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=wait + 10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return -1
    return labels.index(out) if out in labels else -1
