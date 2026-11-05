"""Which commands are test or build runs, and which repo a folder is in.

Agent terminals never wait on each other (Nov 5 2026): TerminalManager runs every command at
once. The per-repo lock below is left only for sandbox_run (backend/sandbox/service.py).
"""

from __future__ import annotations

import os
import re
import threading
from typing import Callable

HEAVY_RE = re.compile(
    r"\b(?:pytest|vitest|npm (?:run )?(?:test|build)|pnpm (?:run )?(?:test|build)|"
    r"cargo (?:test|build|check|clippy|nextest)|rustc|msbuild|dotnet (?:build|test)|gradlew?|"
    r"go (?:build|test)|cmake --build|vite build|build_exes|publish_app|release\.sh|tsc -b)\b"
)
_cond = threading.Condition()
_held: dict[str, str] = {}  # repo -> label of the run holding it


def is_heavy(command: str) -> bool:
    return bool(HEAVY_RE.search(command or ""))


def repo_of(cwd: str) -> str:
    """The git top of cwd (a .git folder or file), else cwd itself; normalized as a key."""
    start = os.path.abspath(cwd or ".")
    path = start
    while True:
        if os.path.exists(os.path.join(path, ".git")):
            return os.path.normcase(path)
        parent = os.path.dirname(path)
        if parent == path:
            return os.path.normcase(start)
        path = parent


def acquire(repo: str, label: str, on_wait: Callable[[str], None] | None = None) -> None:
    """Wait until no run holds repo, then hold it as label. on_wait(holder) is called once
    when it has to wait."""
    told = False
    with _cond:
        while repo in _held:
            if not told and on_wait is not None:
                told = True
                holder = _held[repo]
                _cond.release()
                try:
                    on_wait(holder)
                except Exception:
                    pass
                finally:
                    _cond.acquire()
                continue
            _cond.wait()
        _held[repo] = label


def release(repo: str) -> None:
    with _cond:
        _held.pop(repo, None)
        _cond.notify_all()


def holder(repo: str) -> str:
    """Who holds repo now ('' when free)."""
    with _cond:
        return _held.get(repo, "")
