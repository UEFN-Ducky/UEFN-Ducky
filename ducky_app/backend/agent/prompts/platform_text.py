"""The one platform switch for agent prompt text.

Prompts and hard rules are written once, for Windows. ``for_platform(text)`` returns them
unchanged on Windows; on Linux it swaps the shell words (``py -3`` → ``python3``,
PowerShell → bash, ``%LOCALAPPDATA%`` → ``~/.local/share``) and drops the Windows-only
terminal no-pipe rule. One text source, never a Linux copy.
"""

from __future__ import annotations

import sys

IS_LINUX = sys.platform.startswith("linux")

# Most specific first: each pair is applied in order.
_SWAPS = (
    ("Windows shell: `py -3`.", "Shell: bash, `python3`."),
    ('(shell "bash" or "powershell",', '(shell "bash",'),
    ("Bash, PowerShell,", "Bash,"),
    ("py -3", "python3"),
    ("%LOCALAPPDATA%/", "~/.local/share/"),
    ("PowerShell", "bash"),
    ("powershell", "bash"),
)

# A line naming one of these is a Windows-only rule (the PowerShell no-pipe list).
_WINDOWS_ONLY = ("Select-Object", "Out-Null", "Get-Content", "Get-ChildItem")


def for_platform(text: str, linux: bool | None = None) -> str:
    """``text`` for this OS (``linux`` forces one for tests)."""
    if not (IS_LINUX if linux is None else linux):
        return text
    lines = [ln for ln in text.split("\n") if not any(w in ln for w in _WINDOWS_ONLY)]
    out = "\n".join(lines)
    for old, new in _SWAPS:
        out = out.replace(old, new)
    return out
