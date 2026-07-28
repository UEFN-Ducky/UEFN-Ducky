"""Atomic JSON file writes with rotating backups."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

# Keep at most this many .bak files per target base name
MAX_BACKUPS = 10
MAX_BACKUP_AGE_SEC = 30 * 24 * 3600

BACKUPS_DIR_NAME = "backups"

_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def _lock_for(target: Path) -> threading.Lock:
    key = str(target.resolve())
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
            # Cap growth across long sessions (deleted chats leave orphan keys).
            if len(_locks) > 512:
                for stale in list(_locks.keys())[:64]:
                    if stale == key:
                        continue
                    held = _locks.get(stale)
                    if held is not None and not held.locked():
                        _locks.pop(stale, None)
        return lock


def _app_data_root() -> Path | None:
    try:
        from frontend.settings import default_app_data_dir

        return default_app_data_dir()
    except Exception:
        return None


def _uses_centralized_backups(target: Path) -> bool:
    app_root = _app_data_root()
    if app_root is None:
        return False
    try:
        target.resolve().relative_to(app_root.resolve())
        return True
    except ValueError:
        return False


def _backup_dir_for(target: Path) -> Path:
    """Directory where .bak files for *target* are stored."""
    if not _uses_centralized_backups(target):
        return target.parent
    app_root = _app_data_root()
    assert app_root is not None
    rel = target.resolve().relative_to(app_root.resolve())
    return app_root / BACKUPS_DIR_NAME / rel.parent


def _backup_path_for(target: Path, timestamp: str) -> Path:
    return _backup_dir_for(target) / f"{target.name}.bak.{timestamp}"


def _backup_source_key(bak_path: Path, backups_root: Path) -> str:
    rel = bak_path.relative_to(backups_root)
    parts = list(rel.parts)
    name = parts[-1]
    if ".bak." in name:
        parts[-1] = name.split(".bak.", 1)[0]
    return str(Path(*parts))


def _safe_unlink(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except OSError:
        return False


def _prune_candidate_list(candidates: list[tuple[float, Path]]) -> int:
    removed = 0
    now = time.time()
    for mtime, p in candidates:
        if now - mtime > MAX_BACKUP_AGE_SEC:
            if _safe_unlink(p):
                removed += 1
    candidates = [(m, p) for m, p in candidates if p.exists()]
    candidates.sort(key=lambda x: x[0], reverse=True)
    for _, p in candidates[MAX_BACKUPS:]:
        if _safe_unlink(p):
            removed += 1
    return removed


def prune_backups(target: Path) -> None:
    """Keep newest MAX_BACKUPS backups; remove older than MAX_BACKUP_AGE_SEC."""
    backup_dir = _backup_dir_for(target)
    if not backup_dir.is_dir():
        return
    prefix = target.name + ".bak."
    candidates: list[tuple[float, Path]] = []
    for p in backup_dir.iterdir():
        if not p.is_file():
            continue
        if not p.name.startswith(prefix):
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        candidates.append((mtime, p))
    _prune_candidate_list(candidates)


def prune_all_backups(app_root: Path | None = None) -> int:
    """Prune every backup group under app_root/backups/. Returns files removed."""
    if app_root is None:
        app_root = _app_data_root()
    if app_root is None:
        return 0
    backups_root = app_root / BACKUPS_DIR_NAME
    if not backups_root.is_dir():
        return 0

    groups: dict[str, list[tuple[float, Path]]] = {}
    for p in backups_root.rglob("*"):
        if not p.is_file() or ".bak." not in p.name:
            continue
        key = _backup_source_key(p, backups_root)
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        groups.setdefault(key, []).append((mtime, p))

    removed = 0
    for candidates in groups.values():
        removed += _prune_candidate_list(candidates)
    return removed


def _make_backup(target: Path) -> None:
    if not target.is_file():
        return
    ts = time.strftime("%Y%m%d%H%M%S", time.gmtime())
    bak = _backup_path_for(target, ts)
    bak.parent.mkdir(parents=True, exist_ok=True)
    data = target.read_bytes()
    bak.write_bytes(data)
    prune_backups(target)


def _write_temp(target: Path, payload: str) -> Path:
    tmp = target.parent / f"{target.name}.tmp.{uuid.uuid4().hex}"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    return tmp


def _replace_with_retries(tmp: Path, target: Path) -> None:
    last_err: OSError | None = None
    for attempt in range(12):
        try:
            if not tmp.is_file():
                raise FileNotFoundError(f"temp file missing before replace: {tmp}")
            os.replace(tmp, target)
            return
        except (PermissionError, FileNotFoundError, OSError) as exc:
            last_err = exc
            time.sleep(0.025 * (attempt + 1))
    if last_err is not None:
        raise last_err
    raise OSError(f"atomic replace failed for {target}")


def write_json_atomic(target: Path, data: dict[str, Any], indent: int = 2) -> None:
    """
    Write JSON atomically: backup existing file, write unique temp in same dir, fsync, replace.
    Concurrent writes to the same target are serialized (last writer wins).
    """
    with _lock_for(target):
        target.parent.mkdir(parents=True, exist_ok=True)
        _make_backup(target)
        payload = json.dumps(data, indent=indent, ensure_ascii=False) + "\n"
        tmp = _write_temp(target, payload)
        replaced = False
        try:
            _replace_with_retries(tmp, target)
            replaced = True
        finally:
            if not replaced and tmp.is_file():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        prune_backups(target)


def read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}
