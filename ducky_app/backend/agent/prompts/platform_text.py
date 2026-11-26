"""The one platform switch for agent prompt text.

Prompts and hard rules are written once, for Windows. ``for_platform(text)`` returns them
unchanged on Windows; on Linux it swaps the shell words (``py -3`` → ``python3``,
PowerShell → bash, ``%LOCALAPPDATA%`` → ``~/.local/share``) and drops the Windows-only
terminal no-pipe rule; macOS does the same with zsh, ``~/Library/Application Support``,
``open`` and Cmd shortcuts. One text source, never a Linux or Mac copy.
"""

from __future__ import annotations

import sys

IS_LINUX = sys.platform.startswith("linux")
IS_MAC = sys.platform == "darwin"

# The "never read files through a terminal" list: the POSIX readers, same rule.
_READERS = ("no Get-Content, Select-String, Get-ChildItem, dir, ls, cat, type, rg, grep, findstr,",
            "no cat, less, head, tail, ls, find, rg, grep,")

# Most specific first: each pair is applied in order.
_SWAPS = (
    ("Windows shell: `py -3`.", "Shell: bash, `python3`."),
    ('(shell "bash" or "powershell",', '(shell "bash",'),
    ("Bash, PowerShell,", "Bash,"),
    _READERS,
    ("py -3", "python3"),
    ("%LOCALAPPDATA%/", "~/.local/share/"),
    ("PowerShell", "bash"),
    ("powershell", "bash"),
)

_MAC_SWAPS = (
    ("Windows shell: `py -3`.",
     "You are on macOS; the shell is zsh, `python3`. Open a file or link with `open`, reveal it in "
     "Finder with `open -R`; shortcuts use Cmd, not Ctrl."),
    ('(shell "bash" or "powershell",', '(shell "zsh" or "bash",'),
    ("Bash, PowerShell,", "Bash, zsh,"),
    _READERS,
    ("py -3", "python3"),
    ("%LOCALAPPDATA%/", "~/Library/Application Support/"),
    ("PowerShell", "zsh"),
    ("powershell", "zsh"),
)

# A line naming this is the Windows-only no-pipe rule (PowerShell holds piped output back).
_WINDOWS_ONLY = ("`Select-Object -Last/-First`",)


def for_platform(text: str, linux: bool | None = None, mac: bool | None = None) -> str:
    """``text`` for this OS (``linux`` / ``mac`` force one for tests)."""
    if linux is None and mac is None:
        linux, mac = IS_LINUX, IS_MAC and not IS_LINUX
    if mac:
        swaps = _MAC_SWAPS
    elif linux:
        swaps = _SWAPS
    else:
        return text
    lines = [ln for ln in text.split("\n") if not any(w in ln for w in _WINDOWS_ONLY)]
    out = "\n".join(lines)
    for old, new in swaps:
        out = out.replace(old, new)
    return out
