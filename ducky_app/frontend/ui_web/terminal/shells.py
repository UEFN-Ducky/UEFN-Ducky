"""Resolve bash / PowerShell executables on Windows; bash, zsh, fish and pwsh on Linux."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Literal

TerminalShell = Literal["bash", "powershell", "zsh", "fish"]

_BASH_CANDIDATES = (
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
)


# An agent's PowerShell: no profile and no PSReadLine (nobody edits lines in it). Nov 5 2026
# soak: 5 agent terminals held 463 MB, ~93 MB each against ~35 MB for a bare shell.
_PS_AGENT_ARGS = ("-NoProfile", "-Command", "Remove-Module PSReadLine -ErrorAction SilentlyContinue")

# Linux: the shells a terminal can be, and how an agent's starts light (no rc files). A pty
# makes each interactive by itself.
_POSIX_SHELLS = ("bash", "zsh", "fish")
_POSIX_AGENT_ARGS = {"bash": ("--noprofile", "--norc"), "zsh": ("-f",), "fish": ("--no-config",)}


def _posix() -> bool:
    return sys.platform != "win32"


def _posix_exe(label: str) -> str:
    if label == "powershell":
        return shutil.which("pwsh") or shutil.which("powershell") or ""
    found = shutil.which(label) or ""
    if not found and label == "bash" and Path("/bin/bash").is_file():
        found = "/bin/bash"
    return found


def default_posix_shell() -> str:
    """The person's login shell ($SHELL) when it is one we drive, else bash."""
    name = Path(os.environ.get("SHELL", "") or "").name.lower()
    return name if name in _POSIX_SHELLS and _posix_exe(name) else "bash"


def available_shells() -> list[str]:
    """Shell labels a new terminal can offer on this PC (bash first)."""
    if not _posix():
        return ["bash", "powershell"] if _git_bash() else ["powershell"]
    return ["bash"] + [s for s in ("zsh", "fish", "powershell") if _posix_exe(s)]


def resolve_shell(shell: str, agent: bool = False) -> tuple[str, list[str]]:
    """Return (executable, argv prefix) for the requested shell. ``agent``: start it light."""
    if _posix():
        label = shell_label(shell)
        exe = _posix_exe(label)
        if not exe:
            raise FileNotFoundError(f"{'pwsh' if label == 'powershell' else label} not found on PATH")
        if label == "powershell":
            return exe, [exe, "-NoLogo", "-NoExit", *(_PS_AGENT_ARGS if agent else ())]
        return exe, [exe, *(_POSIX_AGENT_ARGS[label] if agent else ())]
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
    with no error. Asking for powershell always gets PowerShell. Linux: bash, or pwsh when
    asked for and installed."""
    label = shell_label(shell)
    if _posix():
        return "powershell" if label == "powershell" and _posix_exe("powershell") else "bash"
    if label == "bash" and not _git_bash():
        return "powershell"
    return label


def shell_label(shell: str) -> str:
    if _posix():
        name = Path((shell or "").strip()).name.lower()
        if name in ("pwsh", "powershell", "ps"):
            return "powershell"
        return name if name in _POSIX_SHELLS else default_posix_shell()
    name = (shell or "bash").strip().lower()
    if name in ("pwsh", "powershell", "ps"):
        return "powershell"
    return "bash"
