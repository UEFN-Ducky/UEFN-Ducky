"""Disk cache for asset preview PNGs and metadata (per UEFN project)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from frontend.app_paths import resolve_app_data_dir
from frontend.asset_preview.project_scope import current_project_cache_slug
from frontend.settings import PANEL_LISTENER_PORT

PANEL_UI_HTTP_PORT = PANEL_LISTENER_PORT - 1

# Real UEFN exports — not badge/asset/verse placeholders.
_LATEST_MODES = frozenset({"texture", "material", "image"})


@dataclass(frozen=True)
class PreviewRecord:
    preview_id: str
    relative_path: str
    preview_url: str
    mode: str
    asset_class: str
    asset_path: str
    metadata: dict[str, Any]
    verse_source: Optional[str] = None


def cache_dir(*, for_write: bool = False, project_slug: str | None = None) -> Path:
    slug = (project_slug or current_project_cache_slug()).strip() or "_no_project"
    path = resolve_app_data_dir(for_write=for_write) / "asset_previews" / "projects" / slug
    if for_write:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_key(relative_path: str, mtime_ns: int, size: int, *, project_slug: str | None = None) -> str:
    slug = (project_slug or current_project_cache_slug()).strip() or "_no_project"
    raw = f"{slug}\0{relative_path}\0{mtime_ns}\0{size}".encode()
    return hashlib.sha256(raw).hexdigest()


def _latest_pointer_key(relative_path: str, *, project_slug: str | None = None) -> str:
    slug = (project_slug or current_project_cache_slug()).strip() or "_no_project"
    raw = f"{slug}\0{relative_path}".encode()
    return hashlib.sha256(raw).hexdigest()


def _latest_pointer_path(relative_path: str, *, for_write: bool = False, project_slug: str | None = None) -> Path:
    root = cache_dir(for_write=for_write, project_slug=project_slug) / "latest"
    if for_write:
        root.mkdir(parents=True, exist_ok=True)
    return root / f"{_latest_pointer_key(relative_path, project_slug=project_slug)}.json"


def preview_path_for_id(preview_id: str, *, project_slug: str | None = None) -> Path:
    if not preview_id or not all(c in "0123456789abcdef" for c in preview_id) or len(preview_id) != 64:
        raise ValueError("Invalid preview id")
    root = cache_dir(project_slug=project_slug)
    path = root / f"{preview_id}.png"
    if path.is_file():
        return path
    # Legacy flat cache (pre per-project) — read-only fallback.
    legacy = resolve_app_data_dir() / "asset_previews" / f"{preview_id}.png"
    if legacy.is_file():
        return legacy
    return path


def meta_path_for_id(preview_id: str, *, project_slug: str | None = None) -> Path:
    root = cache_dir(project_slug=project_slug)
    path = root / f"{preview_id}.json"
    if path.is_file():
        return path
    legacy = resolve_app_data_dir() / "asset_previews" / f"{preview_id}.json"
    if legacy.is_file():
        return legacy
    return path


def preview_url(preview_id: str) -> str:
    return f"http://127.0.0.1:{PANEL_UI_HTTP_PORT}/asset-previews/{preview_id}.png"


def _record_from_id(preview_id: str, relative_path: str, *, project_slug: str | None = None) -> Optional[PreviewRecord]:
    png = preview_path_for_id(preview_id, project_slug=project_slug)
    meta = meta_path_for_id(preview_id, project_slug=project_slug)
    if not png.is_file() or not meta.is_file():
        return None
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return PreviewRecord(
        preview_id=preview_id,
        relative_path=relative_path,
        preview_url=preview_url(preview_id),
        mode=str(data.get("mode", "image")),
        asset_class=str(data.get("asset_class", "")),
        asset_path=str(data.get("asset_path", "")),
        metadata=dict(data.get("metadata") or {}),
        verse_source=data.get("verse_source"),
    )


def get_cached(relative_path: str, mtime_ns: int, size: int) -> Optional[PreviewRecord]:
    preview_id = _cache_key(relative_path, mtime_ns, size)
    return _record_from_id(preview_id, relative_path)


def get_latest_cached(relative_path: str) -> Optional[PreviewRecord]:
    """Last real preview for this path (survives mtime/size changes)."""
    pointer = _latest_pointer_path(relative_path)
    if not pointer.is_file():
        return None
    try:
        data = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    preview_id = str(data.get("preview_id") or "").strip()
    if not preview_id:
        return None
    return _record_from_id(preview_id, relative_path)


def put_cached(
    relative_path: str,
    mtime_ns: int,
    size: int,
    png_bytes: bytes,
    *,
    mode: str,
    asset_class: str,
    asset_path: str,
    metadata: Optional[dict[str, Any]] = None,
    verse_source: Optional[str] = None,
) -> str:
    preview_id = _cache_key(relative_path, mtime_ns, size)
    root = cache_dir(for_write=True)
    png = root / f"{preview_id}.png"
    meta = root / f"{preview_id}.json"
    png.write_bytes(png_bytes)
    meta.write_text(
        json.dumps(
            {
                "mode": mode,
                "asset_class": asset_class,
                "asset_path": asset_path,
                "metadata": metadata or {},
                "verse_source": verse_source,
                "relative_path": relative_path,
                "project_slug": current_project_cache_slug(),
            },
            indent=0,
        ),
        encoding="utf-8",
    )
    # Badge/verse placeholders must not clobber a real UEFN export pointer.
    if mode in _LATEST_MODES:
        pointer = _latest_pointer_path(relative_path, for_write=True)
        pointer.write_text(
            json.dumps({"preview_id": preview_id, "relative_path": relative_path, "mode": mode}, indent=0),
            encoding="utf-8",
        )
    return preview_id


def record_to_dict(record: PreviewRecord, *, listener_online: bool, stale: bool = False) -> dict[str, Any]:
    out = {
        "mode": record.mode,
        "asset_class": record.asset_class,
        "asset_path": record.asset_path,
        "preview_url": record.preview_url,
        "preview_id": record.preview_id,
        "verse_source": record.verse_source,
        "metadata": record.metadata,
        "listener_online": listener_online,
    }
    if stale:
        out["stale"] = True
    return out
