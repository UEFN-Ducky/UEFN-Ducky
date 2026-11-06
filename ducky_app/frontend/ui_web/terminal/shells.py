"""Resolve bash / PowerShell executables on Windows."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Literal

TerminalShell = Literal["bash", "powershell"]

_BASH_CANDIDATES = (
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
)


# An agent's PowerShell: no profile and no PSReadLine (nobody edits lines in it). Nov 5 2026
# soak: 5 agent terminals held 463 MB, ~93 MB each against ~35 MB for a bare shell.
_PS_AGENT_ARGS = ("-NoProfile", "-Command", "Remove-Module PSReadLine -ErrorAction SilentlyContinue")


def resolve_shell(shell: str, agent: bool = False) -> tuple[str, list[str]]:
    """Return (executable, argv prefix) for the requested shell. ``agent``: start it light."""
    name = (shell or "bash").strip().lower()
    if name in ("pwsh", "powershell", "ps"):
        exe = shutil.which("powershell") or shutil.which("pwsh")
        if not exe and sys.platform == "win32":
            system_root = os.environ.get("SystemRoot", r"C:\Windows")
            exe = str(Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
            if not Path(exe).is_file():
                exe = ""
        if not exe:
            raise FileNotFoundError("PowerShell not found on PATH")
        # -NoExit keeps the interactive shell alive under ConPTY/pywinpty.
        return exe, [exe, "-NoLogo", "-NoExit", *(_PS_AGENT_ARGS if agent else ())]

    exe = _git_bash()
    if not exe:
        raise FileNotFoundError(
            "Git Bash not found. Install Git for Windows or add bash.exe to PATH."
        )
    # Login shell — plain -i can exit immediately under some ConPTY attach sequences. An
    # agent's skips the profiles: Git's bash.exe wrapper sets PATH (usr/bin, mingw64) itself.
    return exe, [exe, "--login", *(("--noprofile", "--norc") if agent else ())]


def _git_bash() -> str:
    for candidate in _BASH_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    which_bash = shutil.which("bash")
    if which_bash and "usr\\bin\\bash" not in which_bash.replace("/", "\\").lower():
        return which_bash
    return ""


def agent_shell(shell: str) -> str:
    """The shell an agent's terminal gets. PowerShell is on every Windows PC; Git Bash is
    optional and never downloaded. Nov 6 2026 (this PC, after 20 commands): light Git Bash
    15.6 MB private against light PowerShell 99.4 MB (test_shell_memory.py), so an agent that
    asks for bash (ducky_terminal_open's default) gets it where installed, else PowerShell,
    with no error. Asking for powershell always gets PowerShell."""
    label = shell_label(shell)
    if label == "bash" and not _git_bash():
        return "powershell"
    return label


def shell_label(shell: str) -> str:
    name = (shell or "bash").strip().lower()
    if name in ("pwsh", "powershell", "ps"):
        return "powershell"
    return "bash"
