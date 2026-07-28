"""Per-project, per-file save snapshots under AppData."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from frontend.settings import PanelSettings, default_app_data_dir
from frontend.ui_web.project_chats import project_slug

MAX_ENTRIES_PER_FILE = 30


def _norm_path(relative_path: str) -> str:
    return (relative_path or "").strip().replace("\\", "/")


def _active_project_root() -> str:
    raw = PanelSettings.load().uefn_project_root.strip()
    if not raw:
        raise ValueError("No project selected.")
    return raw


def _history_root(project_root: str | None = None) -> Path:
    root = project_root if project_root is not None else _active_project_root()
    slug = project_slug(root)
    d = default_app_data_dir() / "file_history" / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def _entries_dir(relative_path: str, project_root: str | None = None) -> Path:
    rel = _norm_path(relative_path)
    if not rel:
        raise ValueError("relative_path is required")
    d = _history_root(project_root) / Path(rel)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _preview_line(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:120]
    return ""


def _entry_id() -> str:
    return f"{int(time.time() * 1000)}"


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _prune_entries(entries_dir: Path) -> None:
    files = sorted(entries_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in files[MAX_ENTRIES_PER_FILE:]:
        try:
            stale.unlink()
        except OSError:
            pass


def _newest_entry(entries_dir: Path) -> dict[str, Any] | None:
    """Most recent snapshot payload (by id, which is a ms timestamp), or None."""
    for path in sorted(entries_dir.glob("*.json"), key=lambda p: p.name, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _entry_hash(entry: dict[str, Any]) -> str | None:
    h = entry.get("content_hash")
    if isinstance(h, str) and h:
        return h
    content = entry.get("content")
    return _content_hash(content) if isinstance(content, str) else None


def _write_snapshot(
    relative_path: str,
    content: str,
    project_root: str | None = None,
    source: str | None = None,
) -> str:
    rel = _norm_path(relative_path)
    entries_dir = _entries_dir(rel, project_root)
    # Dedup: never create a new save point when the content is identical to the most recent
    # one. Otherwise just browsing/restoring history (which round-trips through the editor +
    # autosave) spams redundant snapshots of versions already recorded. Return the existing id.
    newest = _newest_entry(entries_dir)
    if newest is not None and _entry_hash(newest) == _content_hash(content):
        return str(newest.get("id") or "")
    entry_id = _entry_id()
    # Two writes in the same ms (before + after an agent edit) must not share a filename.
    while (entries_dir / f"{entry_id}.json").is_file():
        entry_id = str(int(entry_id) + 1)
    payload: dict[str, Any] = {
        "id": entry_id,
        "path": rel,
        "saved_at": int(time.time()),
        "bytes": len(content.encode("utf-8")),
        "preview": _preview_line(content),
        "content_hash": _content_hash(content),
        "content": content,
    }
    if source:
        payload["source"] = source
    target = entries_dir / f"{entry_id}.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    _prune_entries(entries_dir)
    return entry_id


def snapshot_before_write(
    relative_path: str,
    old_content: str,
    project_root: str | None = None,
    source: str | None = None,
) -> None:
    """Store the previous on-disk content before overwriting."""
    _write_snapshot(relative_path, old_content, project_root, source=source)


def snapshot_editor_content(
    relative_path: str,
    content: str,
    project_root: str | None = None,
) -> dict[str, str]:
    """Store current editor content so the user can return after restoring an older version."""
    rel = _norm_path(relative_path)
    entry_id = _write_snapshot(rel, content, project_root)
    return {"id": entry_id, "path": rel}


def record_agent_write(
    relative_path: str,
    before: str,
    after: str,
    project_root: str | None = None,
) -> None:
    """Snapshot pre-AI content (restore point) and the AI-written version (source=agent)."""
    rel = _norm_path(relative_path)
    if before and before != after:
        _write_snapshot(rel, before, project_root)
    _write_snapshot(rel, after, project_root, source="agent")


def list_entries(relative_path: str, project_root: str | None = None) -> list[dict[str, Any]]:
    rel = _norm_path(relative_path)
    entries_dir = _entries_dir(rel, project_root)
    out: list[dict[str, Any]] = []
    for path in sorted(entries_dir.glob("*.json"), key=lambda p: p.name, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        content = data.get("content")
        content_hash = data.get("content_hash")
        if not isinstance(content_hash, str) and isinstance(content, str):
            content_hash = _content_hash(content)
        src = data.get("source")
        out.append(
            {
                "id": str(data.get("id") or path.stem),
                "path": str(data.get("path") or rel),
                "saved_at": int(data.get("saved_at") or 0),
                "bytes": int(data.get("bytes") or 0),
                "preview": str(data.get("preview") or ""),
                "content_hash": str(content_hash or ""),
                "source": str(src) if isinstance(src, str) and src else "",
            }
        )
    return out


def read_entry(relative_path: str, entry_id: str, project_root: str | None = None) -> dict[str, str]:
    rel = _norm_path(relative_path)
    eid = (entry_id or "").strip()
    if not eid:
        raise ValueError("entry_id is required")
    target = _entries_dir(rel, project_root) / f"{eid}.json"
    if not target.is_file():
        raise ValueError(f"History entry not found: {eid}")
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid history entry: {eid}")
    content = data.get("content")
    if not isinstance(content, str):
        raise ValueError(f"History entry has no content: {eid}")
    return {"content": content, "path": rel, "id": eid}
