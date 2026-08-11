"""Per-project Verse diagnostics cache (memory + pruned disk).

Disk lives under %LOCALAPPDATA%/UEFN-Ducky/verse_diagnostics/<slug>/.ducky_verse_scan.json
so a restart can skip unchanged files. Ghost Problems from deleted files are avoided by:

- CACHE_VERSION bump (ignore old unpruned caches)
- prune_deleted() on every load / load_for_ui / save
- mtime+size fingerprints so edited files re-scan
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
from pathlib import Path
from typing import Any

CACHE_VERSION = 3  # bump: v2 could persist false "0 errors" from empty pulls
CACHE_DIR_NAME = "verse_diagnostics"
CACHE_FILE_NAME = ".ducky_verse_scan.json"

_mem_lock = threading.Lock()
_MEM: dict[str, dict[str, Any]] = {}
# Disk-file fingerprint (mtime_ns, size) observed when _MEM was last loaded/saved —
# so an external clear/rescan (another process) is picked up instead of resurrecting
# poisoned RAM over a clean on-disk snapshot.
_MEM_DISK_FP: dict[str, tuple[int, int]] = {}
_legacy_purged = False


def _disk_fingerprint(project_root: str) -> tuple[int, int] | None:
    path = _disk_path(project_root)
    try:
        st = path.stat()
    except OSError:
        return None
    return int(st.st_mtime_ns), int(st.st_size)


def cache_enabled() -> bool:
    return True


def _norm_key(path: str) -> str:
    return path.strip().replace("\\", "/").lstrip("/").lower()


def _norm_root(project_root: str) -> str:
    return str(project_root or "").strip().replace("\\", "/").rstrip("/").lower()


def _cache_dir() -> Path:
    from frontend.settings import default_app_data_dir

    return default_app_data_dir() / CACHE_DIR_NAME


def _slug_for_root(project_root: str) -> str:
    return hashlib.sha256(_norm_root(project_root).encode("utf-8")).hexdigest()[:16]


def _disk_path(project_root: str) -> Path:
    return _cache_dir() / _slug_for_root(project_root) / CACHE_FILE_NAME


def purge_legacy_v1_caches() -> None:
    """Drop v1 on-disk caches once (they could keep ghost deleted-file errors)."""
    global _legacy_purged
    if _legacy_purged:
        return
    _legacy_purged = True
    root = _cache_dir()
    if not root.is_dir():
        return
    try:
        for path in root.rglob(CACHE_FILE_NAME):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                path.unlink(missing_ok=True)
                continue
            if not isinstance(raw, dict) or int(raw.get("v") or 0) != CACHE_VERSION:
                path.unlink(missing_ok=True)
    except Exception:
        pass


def _empty() -> dict[str, Any]:
    return {"v": CACHE_VERSION, "files": {}}


def _read_disk(project_root: str) -> dict[str, Any] | None:
    path = _disk_path(project_root)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict) or int(raw.get("v") or 0) != CACHE_VERSION:
        return None
    files = raw.get("files")
    if not isinstance(files, dict):
        return None
    return {"v": CACHE_VERSION, "files": files}


def _write_disk(project_root: str, data: dict[str, Any]) -> None:
    path = _disk_path(project_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"v": CACHE_VERSION, "files": data.get("files") or {}}
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def load(project_root: str) -> dict[str, Any]:
    """Return THE live in-memory state dict for this project (mutations stick)."""
    purge_legacy_v1_caches()
    key = _norm_root(project_root)
    with _mem_lock:
        store = _MEM.get(key)
        disk_fp = _disk_fingerprint(project_root)
        mem_fp = _MEM_DISK_FP.get(key)
        disk = _read_disk(project_root)
        if store is not None and disk_fp is not None and mem_fp == disk_fp:
            return store
        if store is not None and disk is None and (store.get("files") or {}):
            # Disk cache was deleted (maintenance / manual clear) — drop RAM zombies
            # so the next Problems read cannot resurrect stale errors.
            store = _empty()
            _MEM[key] = store
            _MEM_DISK_FP.pop(key, None)
            return store
        # Cold start, or disk changed under us (another process wrote a cleaner scan).
        store = disk if disk is not None else _empty()
        prune_deleted(project_root, store)
        _MEM[key] = store
        if disk_fp is not None:
            _MEM_DISK_FP[key] = disk_fp
        else:
            _MEM_DISK_FP.pop(key, None)
        return store


def save(project_root: str, data: dict[str, Any]) -> None:
    """Adopt data into memory (if needed) and persist pruned snapshot to disk."""
    key = _norm_root(project_root)
    with _mem_lock:
        current = _MEM.get(key)
        if current is not data and isinstance(data, dict):
            store = {"v": CACHE_VERSION, "files": data.get("files") or {}}
            _MEM[key] = store
        else:
            store = current if current is not None else _empty()
            _MEM[key] = store
        prune_deleted(project_root, store)
        _write_disk(project_root, store)
        disk_fp = _disk_fingerprint(project_root)
        if disk_fp is not None:
            _MEM_DISK_FP[key] = disk_fp
        else:
            _MEM_DISK_FP.pop(key, None)


def clear(project_root: str | None = None) -> None:
    """Drop session + disk state for one project (or all)."""
    with _mem_lock:
        if project_root is None:
            _MEM.clear()
            _MEM_DISK_FP.clear()
            try:
                shutil.rmtree(_cache_dir(), ignore_errors=True)
            except Exception:
                pass
            return
        key = _norm_root(project_root)
        _MEM.pop(key, None)
        _MEM_DISK_FP.pop(key, None)
    try:
        path = _disk_path(project_root)
        path.unlink(missing_ok=True)
        parent = path.parent
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except Exception:
        pass


def fingerprint(abs_path: Path) -> tuple[int, int]:
    st = abs_path.stat()
    return int(st.st_mtime_ns), int(st.st_size)


def _disk_files(project_root: str) -> dict[str, Path]:
    content = Path(project_root) / "Content"
    if not content.is_dir():
        return {}
    out: dict[str, Path] = {}
    for path in content.rglob("*.verse"):
        if not path.is_file():
            continue
        rel = str(path.relative_to(Path(project_root))).replace("\\", "/")
        out[_norm_key(rel)] = path
    return out


def is_fresh(entry: dict[str, Any], abs_path: Path) -> bool:
    try:
        mtime_ns, size = fingerprint(abs_path)
    except OSError:
        return False
    return entry.get("mtime_ns") == mtime_ns and entry.get("size") == size


def stale_keys(project_root: str, cache: dict[str, Any] | None = None) -> list[str]:
    """Keys that need a verse-lsp recheck.

    Unchanged clean files stay fresh (mtime+size). Cached *problems* are never
    sticky: a flaky/false-positive diagnosis used to pin red Problems forever
    because the file's fingerprint never changed and incremental scans skipped it
    — while UEFN's real compile stayed green. Always revalidate error/warning
    entries so the next open/edit/rescan can clear ghosts.
    """
    cache = cache if cache is not None else load(project_root)
    cached = cache.get("files") or {}
    disk = _disk_files(project_root)
    stale: list[str] = []
    for key, path in disk.items():
        entry = cached.get(key)
        if not isinstance(entry, dict) or not is_fresh(entry, path):
            stale.append(key)
            continue
        if int(entry.get("errors") or 0) > 0 or int(entry.get("warnings") or 0) > 0:
            stale.append(key)
    return stale


def files_for_ui(cache: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    files = cache.get("files") or {}
    for key in sorted(files):
        entry = files[key]
        if not isinstance(entry, dict):
            continue
        out.append(
            {
                "path": key,
                "errors": int(entry.get("errors") or 0),
                "warnings": int(entry.get("warnings") or 0),
                "items": list(entry.get("items") or []),
            }
        )
    return out


def apply_file(
    project_root: str,
    cache: dict[str, Any],
    key: str,
    fp: tuple[int, int],
    result: dict[str, Any],
    *,
    persist: bool = True,
) -> None:
    files = cache.setdefault("files", {})
    mtime_ns, size = fp
    files[_norm_key(key)] = {
        "mtime_ns": mtime_ns,
        "size": size,
        "errors": int(result.get("errors") or 0),
        "warnings": int(result.get("warnings") or 0),
        "items": list(result.get("items") or []),
    }
    if persist:
        save(project_root, cache)


def prune_deleted(project_root: str, cache: dict[str, Any]) -> None:
    disk = _disk_files(project_root)
    files = cache.get("files") or {}
    for key in list(files):
        if key not in disk:
            del files[key]


def load_for_ui(project_root: str) -> dict[str, Any]:
    """Authoritative snapshot: cache pruned against disk + stale count."""
    cache = load(project_root)
    prune_deleted(project_root, cache)
    stale = stale_keys(project_root, cache)
    return {
        "files": files_for_ui(cache),
        "stale_count": len(stale),
        "from_cache": True,
    }
