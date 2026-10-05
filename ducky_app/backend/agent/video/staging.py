"""Composer uploads land here once; the composer keeps only the staged id."""

from __future__ import annotations

import base64
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from backend.agent.video.frames import VideoError
from backend.agent.video.limits import video_limits
from frontend.app_paths import resolve_app_data_dir

VIDEO_MIME_EXT = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "video/x-matroska": ".mkv",
}
_EXT_MIME = {ext: mime for mime, ext in VIDEO_MIME_EXT.items()}
_STAGED_RE = re.compile(r"^[0-9a-f]{32}\.(?:mp4|webm|mov|mkv)$")
_DATA_URL_RE = re.compile(r"^data:[^;]*;base64,")
_MAX_AGE_S = 24 * 3600


def normalize_video_mime(mime: str, name: str) -> str | None:
    m = (mime or "").strip().lower()
    if m in VIDEO_MIME_EXT:
        return m
    return _EXT_MIME.get(Path(name or "").suffix.lower())


def staging_dir(create: bool = False) -> Path:
    path = resolve_app_data_dir(for_write=create) / "video_staging"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _sweep(root: Path) -> None:
    cutoff = time.time() - _MAX_AGE_S
    for p in root.glob("*"):
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


def stage_video(name: str, mime: str, data_base64: str) -> dict[str, Any]:
    norm = normalize_video_mime(mime, name)
    if norm is None:
        raise VideoError(f"Unsupported video format for {name!r} — use MP4, WebM, MOV or MKV.")
    try:
        raw = base64.b64decode(_DATA_URL_RE.sub("", (data_base64 or "").strip()), validate=True)
    except Exception as exc:
        raise VideoError(f"Could not read video {name!r}.") from exc
    limit = video_limits().max_bytes
    if len(raw) > limit:
        raise VideoError(f"Video {name!r} exceeds the {limit // (1024 * 1024)}MB video limit.")
    root = staging_dir(create=True)
    _sweep(root)
    staged_id = uuid.uuid4().hex + VIDEO_MIME_EXT[norm]
    (root / staged_id).write_bytes(raw)
    return {"staged_id": staged_id, "size_bytes": len(raw), "mime": norm}


def resolve_staged(staged_id: str) -> Path:
    sid = (staged_id or "").strip()
    path = staging_dir() / sid
    if not _STAGED_RE.fullmatch(sid) or not path.is_file():
        raise VideoError("The video upload expired — attach the video again.")
    try:
        os.utime(path)
    except OSError:
        pass
    return path


def safe_media_path(path: str | Path, *, suffixes: set[str] | frozenset[str]) -> Path | None:
    """Resolve a client-supplied media path, or None unless it is a regular file with an
    allowed suffix that sits directly in video_staging/ or in a chat's attachments/ folder."""
    try:
        target = Path(path).resolve()
        app = resolve_app_data_dir().resolve()
        rel_parts = target.relative_to(app).parts
    except (OSError, ValueError):
        return None
    if target.suffix.lower() not in suffixes or not target.is_file():
        return None
    in_staging = len(rel_parts) == 2 and rel_parts[0] == "video_staging"
    in_chat = len(rel_parts) >= 3 and rel_parts[0] == "chats" and target.parent.name == "attachments"
    return target if (in_staging or in_chat) else None
