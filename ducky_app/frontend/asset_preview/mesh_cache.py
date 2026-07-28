"""Bounded Local AppData cache for on-demand StaticMesh FBX previews."""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from frontend.app_paths import resolve_app_data_dir
from frontend.asset_preview.project_scope import current_project_cache_slug
from frontend.settings import PANEL_LISTENER_PORT

PANEL_UI_HTTP_PORT = PANEL_LISTENER_PORT - 1

# Keep disk usage bounded — only explicit user loads populate this cache.
_MAX_ENTRIES = 32
_MODEL_FILENAME = "model.fbx"
_META_FILENAME = "meta.json"

_lock = threading.Lock()
_inflight: dict[str, threading.Event] = {}
_inflight_results: dict[str, dict[str, Any]] = {}
_inflight_errors: dict[str, BaseException] = {}


@dataclass(frozen=True)
class MeshPreviewRecord:
    cache_id: str
    relative_path: str
    asset_path: str
    filename: str
    media_url: str
    media_base_url: str
    media_filename: str
    metadata: dict[str, Any]
    from_cache: bool


def mesh_cache_root(*, for_write: bool = False, project_slug: str | None = None) -> Path:
    slug = (project_slug or current_project_cache_slug()).strip() or "_no_project"
    path = resolve_app_data_dir(for_write=for_write) / "mesh_previews" / "projects" / slug
    if for_write:
        path.mkdir(parents=True, exist_ok=True)
    return path


def cache_key(relative_path: str, mtime_ns: int, size: int, *, project_slug: str | None = None) -> str:
    slug = (project_slug or current_project_cache_slug()).strip() or "_no_project"
    raw = f"mesh\0{slug}\0{relative_path}\0{mtime_ns}\0{size}".encode()
    return hashlib.sha256(raw).hexdigest()


def _cache_key(relative_path: str, mtime_ns: int, size: int) -> str:
    return cache_key(relative_path, mtime_ns, size)


def _latest_pointer_key(relative_path: str, *, project_slug: str | None = None) -> str:
    slug = (project_slug or current_project_cache_slug()).strip() or "_no_project"
    raw = f"mesh\0{slug}\0{relative_path}".encode()
    return hashlib.sha256(raw).hexdigest()


def _latest_pointer_path(relative_path: str, *, for_write: bool = False, project_slug: str | None = None) -> Path:
    root = mesh_cache_root(for_write=for_write, project_slug=project_slug) / "latest"
    if for_write:
        root.mkdir(parents=True, exist_ok=True)
    return root / f"{_latest_pointer_key(relative_path, project_slug=project_slug)}.json"


def cache_dir_for_id(cache_id: str, *, for_write: bool = False, project_slug: str | None = None) -> Path:
    if not cache_id or len(cache_id) != 64 or not all(c in "0123456789abcdef" for c in cache_id):
        raise ValueError("Invalid mesh cache id")
    path = mesh_cache_root(for_write=for_write, project_slug=project_slug) / cache_id
    if for_write:
        path.mkdir(parents=True, exist_ok=True)
    return path


def mesh_preview_url(cache_id: str, filename: str = _MODEL_FILENAME) -> str:
    safe_name = Path(filename).name
    return f"http://127.0.0.1:{PANEL_UI_HTTP_PORT}/mesh-previews/{cache_id}/{safe_name}"


def mesh_preview_base_url(cache_id: str) -> str:
    return f"http://127.0.0.1:{PANEL_UI_HTTP_PORT}/mesh-previews/{cache_id}/"


def resolve_mesh_preview_path(cache_id: str, filename: str) -> Path:
    """Map a /mesh-previews/<id>/<name> request to a file under the cache dir."""
    name = Path(filename or "").name
    if not name or name in {".", ".."}:
        raise ValueError("Invalid mesh preview filename")
    root = cache_dir_for_id(cache_id).resolve()
    target = (root / name).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("Path escapes mesh cache") from exc
    if target.is_file():
        return target
    # Legacy flat mesh cache (pre per-project).
    legacy_root = (resolve_app_data_dir() / "mesh_previews" / cache_id).resolve()
    legacy = (legacy_root / name).resolve()
    try:
        legacy.relative_to(legacy_root)
    except ValueError as exc:
        raise ValueError("Path escapes mesh cache") from exc
    if legacy.is_file():
        return legacy
    raise ValueError("Not a file")


def _record_from_cache_id(cache_id: str, relative_path: str) -> Optional[MeshPreviewRecord]:
    directory = cache_dir_for_id(cache_id)
    model = directory / _MODEL_FILENAME
    meta_path = directory / _META_FILENAME
    if not model.is_file() or not meta_path.is_file():
        return None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    filename = str(data.get("filename") or _MODEL_FILENAME)
    if not (directory / filename).is_file():
        filename = _MODEL_FILENAME
        if not model.is_file():
            return None
    # Touch directory mtime for LRU pruning.
    try:
        now = time.time()
        os_utime = getattr(__import__("os"), "utime")
        os_utime(directory, (now, now))
    except OSError:
        pass
    return MeshPreviewRecord(
        cache_id=cache_id,
        relative_path=relative_path,
        asset_path=str(data.get("asset_path") or ""),
        filename=filename,
        media_url=mesh_preview_url(cache_id, filename),
        media_base_url=mesh_preview_base_url(cache_id),
        media_filename=filename,
        metadata=dict(data.get("metadata") or {}),
        from_cache=True,
    )


def get_cached_mesh(relative_path: str, mtime_ns: int, size: int) -> Optional[MeshPreviewRecord]:
    return _record_from_cache_id(_cache_key(relative_path, mtime_ns, size), relative_path)


def get_latest_cached_mesh(relative_path: str) -> Optional[MeshPreviewRecord]:
    """Last mesh export for this path (survives mtime/size changes; dangling pointers = miss)."""
    pointer = _latest_pointer_path(relative_path)
    if not pointer.is_file():
        return None
    try:
        data = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    cache_id = str(data.get("cache_id") or "").strip()
    if not cache_id:
        return None
    return _record_from_cache_id(cache_id, relative_path)


def put_cached_mesh(
    relative_path: str,
    mtime_ns: int,
    size: int,
    *,
    asset_path: str,
    exported_file: str,
    filename: str,
    metadata: Optional[dict[str, Any]] = None,
    siblings_dir: Optional[str] = None,
) -> MeshPreviewRecord:
    cache_id = _cache_key(relative_path, mtime_ns, size)
    directory = cache_dir_for_id(cache_id, for_write=True)
    src = Path(exported_file)
    if not src.is_file():
        raise ValueError(f"Exported mesh file missing: {exported_file}")

    # Prefer copying the whole export directory so sibling textures stay available.
    source_dir = Path(siblings_dir) if siblings_dir else src.parent
    if source_dir.resolve() != directory.resolve():
        for child in list(directory.iterdir()):
            if child.is_file():
                child.unlink(missing_ok=True)
        for child in source_dir.iterdir():
            if not child.is_file():
                continue
            dest = directory / child.name
            if child.resolve() == dest.resolve():
                continue
            shutil.copy2(child, dest)
        # Ensure canonical model.fbx name for stable URLs when possible.
        dest_model = directory / _MODEL_FILENAME
        if src.name.lower().endswith(".fbx") and not dest_model.is_file():
            candidate = directory / src.name
            if candidate.is_file():
                shutil.copy2(candidate, dest_model)
        elif src.is_file() and src.resolve().parent == source_dir.resolve():
            if not dest_model.is_file():
                shutil.copy2(src, dest_model)

    final_name = _MODEL_FILENAME if (directory / _MODEL_FILENAME).is_file() else Path(filename).name
    if not (directory / final_name).is_file():
        shutil.copy2(src, directory / final_name)
        final_name = final_name

    meta = {
        "relative_path": relative_path,
        "asset_path": asset_path,
        "filename": final_name,
        "metadata": metadata or {},
        "cached_at": time.time(),
    }
    (directory / _META_FILENAME).write_text(json.dumps(meta, indent=0), encoding="utf-8")
    pointer = _latest_pointer_path(relative_path, for_write=True)
    pointer.write_text(
        json.dumps({"cache_id": cache_id, "relative_path": relative_path}, indent=0),
        encoding="utf-8",
    )
    prune_mesh_cache(_MAX_ENTRIES)
    return MeshPreviewRecord(
        cache_id=cache_id,
        relative_path=relative_path,
        asset_path=asset_path,
        filename=final_name,
        media_url=mesh_preview_url(cache_id, final_name),
        media_base_url=mesh_preview_base_url(cache_id),
        media_filename=final_name,
        metadata=dict(metadata or {}),
        from_cache=False,
    )


def prune_mesh_cache(max_entries: int = _MAX_ENTRIES) -> int:
    """Delete oldest cache dirs when over the entry cap. Returns removed count."""
    root = mesh_cache_root(for_write=False)
    if not root.is_dir():
        return 0
    entries: list[tuple[float, Path]] = []
    for child in root.iterdir():
        if not child.is_dir() or len(child.name) != 64:
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            continue
        entries.append((mtime, child))
    if len(entries) <= max_entries:
        return 0
    entries.sort(key=lambda item: item[0])
    remove_n = len(entries) - max_entries
    removed = 0
    for _, path in entries[:remove_n]:
        try:
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
        except OSError:
            pass
    return removed


def begin_inflight(cache_id: str) -> tuple[bool, threading.Event]:
    """Claim an in-flight slot. Returns (is_leader, event)."""
    with _lock:
        existing = _inflight.get(cache_id)
        if existing is not None:
            return False, existing
        event = threading.Event()
        _inflight[cache_id] = event
        _inflight_results.pop(cache_id, None)
        _inflight_errors.pop(cache_id, None)
        return True, event


def finish_inflight(cache_id: str, result: Optional[dict[str, Any]] = None, error: Optional[BaseException] = None) -> None:
    with _lock:
        if result is not None:
            _inflight_results[cache_id] = result
        if error is not None:
            _inflight_errors[cache_id] = error
        event = _inflight.pop(cache_id, None)
    if event is not None:
        event.set()


def wait_inflight(cache_id: str, event: threading.Event, timeout: float = 120.0) -> dict[str, Any]:
    if not event.wait(timeout):
        raise TimeoutError("Timed out waiting for mesh preview export")
    with _lock:
        error = _inflight_errors.get(cache_id)
        result = _inflight_results.get(cache_id)
    if error is not None:
        raise error
    if result is None:
        raise RuntimeError("Mesh preview export finished without a result")
    return result


def record_to_dict(record: MeshPreviewRecord, *, listener_online: bool, stale: bool = False) -> dict[str, Any]:
    out = {
        "ok": True,
        "cache_id": record.cache_id,
        "relative_path": record.relative_path,
        "asset_path": record.asset_path,
        "asset_class": "StaticMesh",
        "media_url": record.media_url,
        "media_base_url": record.media_base_url,
        "media_filename": record.media_filename,
        "mime": "model/fbx",
        "kind": "model",
        "metadata": record.metadata,
        "from_cache": record.from_cache,
        "listener_online": listener_online,
    }
    if stale:
        out["stale"] = True
    return out
