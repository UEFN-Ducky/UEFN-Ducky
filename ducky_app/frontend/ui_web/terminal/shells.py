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


def resolve_shell(shell: str) -> tuple[str, list[str]]:
    """Return (executable, argv prefix) for the requested shell."""
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
        return exe, [exe, "-NoLogo", "-NoExit"]

    exe = ""
    for candidate in _BASH_CANDIDATES:
        if Path(candidate).is_file():
            exe = candidate
            break
    if not exe:
        which_bash = shutil.which("bash")
        if which_bash and "usr\\bin\\bash" not in which_bash.replace("/", "\\").lower():
            exe = which_bash
    if not exe:
        raise FileNotFoundError(
            "Git Bash not found. Install Git for Windows or add bash.exe to PATH."
        )
    # Login shell — plain -i can exit immediately under some ConPTY attach sequences.
    return exe, [exe, "--login"]


def shell_label(shell: str) -> str:
    name = (shell or "bash").strip().lower()
    if name in ("pwsh", "powershell", "ps"):
        return "powershell"
    return "bash"
