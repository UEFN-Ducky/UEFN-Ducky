"""Where each chat's sandbox lives, how big it is, and when an old one goes."""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

_CONV_ID = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
SUBDIRS = ("work", "downloads", "logs", "home", "appdata/Local", "appdata/Roaming", "temp")
# Unused this long (no file touched) and the sandbox is removed by prune().
PRUNE_AFTER_S = 14 * 24 * 3600
SIZE_WARN_BYTES = 5 * 1024**3
_TOUCH = ".last_used"


def root() -> Path:
    from frontend.settings import default_app_data_dir

    return default_app_data_dir() / "sandboxes"


def tools_root() -> Path:
    """Shared, hash-checked portable runtimes (one copy for every sandbox)."""
    from frontend.settings import default_app_data_dir

    return default_app_data_dir() / "tools" / "sandbox"


def valid_conv(conv_id: str) -> str:
    cid = (conv_id or "").strip()
    if not _CONV_ID.fullmatch(cid) or cid in (".", ".."):
        raise ValueError("A sandbox belongs to a chat, and no chat is open for this call.")
    return cid


def sandbox_dir(conv_id: str, *, create: bool = False) -> Path:
    path = root() / valid_conv(conv_id)
    if create:
        for sub in SUBDIRS:
            (path / sub).mkdir(parents=True, exist_ok=True)
        touch(path)
    return path


def exists(conv_id: str) -> bool:
    try:
        return (sandbox_dir(conv_id) / "work").is_dir()
    except ValueError:
        return False


def touch(path: Path) -> None:
    try:
        (path / _TOUCH).write_text(str(int(time.time())), encoding="utf-8")
    except OSError:
        pass


def last_used(path: Path) -> float:
    try:
        return float((path / _TOUCH).read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def is_inside(path: str, folder: Path | str) -> bool:
    try:
        full, base = _norm(str(path)), _norm(str(folder))
        return os.path.commonpath([full, base]) == base
    except (ValueError, OSError):
        return False


def owner_of(path: str) -> str:
    """The chat whose sandbox holds ``path``, or ''."""
    base = root()
    if not is_inside(path, base):
        return ""
    rel = os.path.relpath(_norm(path), _norm(str(base)))
    first = rel.split(os.sep, 1)[0]
    return first if _CONV_ID.fullmatch(first) else ""


def is_sandbox_path(path: str, conv_id: str = "") -> bool:
    """True when ``path`` is inside a sandbox (that chat's own, when ``conv_id`` is given)."""
    owner = owner_of(path)
    return bool(owner) and (not conv_id or owner == conv_id)


def dir_size(path: Path, *, cap_files: int = 200_000) -> int:
    total = 0
    seen = 0
    for dirpath, dirnames, filenames in os.walk(path):
        # Junctions/symlinks (node_modules links, worktree .git files) are counted once, never followed.
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        for name in filenames:
            seen += 1
            if seen > cap_files:
                return total
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return total


def is_chat_sandbox(path: Path) -> bool:
    """A folder this service made for a chat: its marker, or its own layout (older ones have
    no marker). The release workflows keep folders of their own beside the sandboxes
    (``duckyos-deploy``, ``release-store-plugin-<id>``: git worktrees made from a terminal).
    They belong to no chat, so they are never a sandbox: until Nov 12 2026 the hourly prune
    took each one for the sandbox of a deleted chat and deleted it, also in the middle of a
    deploy ("awk: cannot open file 'duckyos/Cargo.toml'", core 0.1.806)."""
    return (path / _TOUCH).is_file() or all((path / sub).is_dir() for sub in ("work", "home", "logs"))


def list_sandboxes() -> list[dict[str, Any]]:
    base = root()
    if not base.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for child in sorted(base.iterdir()):
        if child.is_dir() and _CONV_ID.fullmatch(child.name) and is_chat_sandbox(child):
            out.append({"conv_id": child.name, "path": str(child), "last_used": last_used(child)})
    return out


def remove_tree(path: Path) -> list[str]:
    """Delete a folder tree; returns paths that could not be removed (a program still has them open)."""
    failed: list[str] = []

    def onerror(_func: Any, p: str, _exc: Any) -> None:
        try:
            os.chmod(p, 0o700)
            os.unlink(p) if os.path.isfile(p) else os.rmdir(p)
        except OSError:
            failed.append(p)

    if path.exists():
        shutil.rmtree(path, onexc=onerror)
    return failed


def stale(now: float | None = None, *, chat_exists: Any = None) -> list[str]:
    """Chats whose sandbox is unused for PRUNE_AFTER_S, or whose chat was deleted."""
    now = time.time() if now is None else now
    out: list[str] = []
    for row in list_sandboxes():
        gone = chat_exists is not None and not chat_exists(row["conv_id"])
        if gone or now - float(row["last_used"] or 0) > PRUNE_AFTER_S:
            out.append(row["conv_id"])
    return out
