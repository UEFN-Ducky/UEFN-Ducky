"""Linux and macOS: find CLIs (claude, codex, gemini, npx, uvx) the way a login shell would.

A desktop-launched app (Linux desktop, macOS Finder/Dock) gets a bare PATH: no nvm, no
~/.local/bin, no ~/.npm-global/bin, on macOS no Homebrew. These helpers add those folders
and resolve names with ``shutil.which``. Windows never calls them (callers gate on ``IS_POSIX``).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

IS_LINUX = sys.platform.startswith("linux")
IS_POSIX = sys.platform != "win32"


def _nvm_bins(home: Path) -> list[str]:
    root = Path(os.environ.get("NVM_DIR") or home / ".nvm") / "versions" / "node"
    try:
        vers = sorted((p for p in root.iterdir() if (p / "bin").is_dir()),
                      key=lambda p: [int(x) if x.isdigit() else 0 for x in p.name.lstrip("v").split(".")],
                      reverse=True)
    except OSError:
        return []
    return [str(v / "bin") for v in vers]


def login_dirs(home: str | Path | None = None) -> list[str]:
    """Folders a login shell usually adds, newest nvm node first; only ones that exist."""
    h = Path(home or Path.home())
    dirs = [str(h / ".local" / "bin"), str(h / ".npm-global" / "bin"), *_nvm_bins(h),
            str(h / ".cargo" / "bin"), str(h / "bin"), "/usr/local/bin", "/usr/bin", "/bin"]
    return [d for d in dirs if os.path.isdir(d)]


def login_path(path: str | None = None, home: str | Path | None = None) -> str:
    """``path`` (default: this process's PATH) with :func:`login_dirs` appended once.
    macOS: the login shell's PATH first, then ``path``, then Homebrew's folders (as the terminal)."""
    base = os.environ.get("PATH", "") if path is None else path
    chunks = [base]
    if sys.platform == "darwin":
        from frontend.ui_web.terminal import path_env

        chunks = [path_env.posix_login_path(path_env._login_shell()), base, *path_env._MAC_EXTRA_PATH]
    parts: list[str] = []
    for chunk in chunks:
        for p in chunk.split(os.pathsep):
            if p and p not in parts:
                parts.append(p)
    for d in login_dirs(home):
        if d not in parts:
            parts.append(d)
    return os.pathsep.join(parts)


def which(name: str, path: str | None = None) -> str:
    """Absolute path of ``name`` on the login PATH, or ''."""
    return shutil.which(name, path=login_path(path)) or ""


def posix_stdio_block(block: dict[str, Any]) -> dict[str, Any]:
    """A stdio server block made runnable on Linux: Windows ``cmd /c X …`` wrappers
    unwrapped, the command resolved on the login PATH, and that PATH passed to the child.
    argv stays a list (no shell, no quoting)."""
    cmd = str(block.get("command") or "")
    args = [str(a) for a in (block.get("args") or [])]
    if Path(cmd).name.lower() in ("cmd", "cmd.exe") and args and args[0].lower() == "/c":
        cmd, args = (args[1], args[2:]) if len(args) > 1 else (cmd, args)
    if cmd.lower().endswith((".exe", ".cmd")):
        cmd = cmd[:-4]
    env = dict(block.get("env") or {})
    env["PATH"] = login_path(env.get("PATH"))
    resolved = shutil.which(cmd, path=env["PATH"]) or cmd
    # mcp's stdio_client layers this env over its safe default env (HOME, USER, …).
    return {**block, "command": resolved, "args": args, "env": env}
