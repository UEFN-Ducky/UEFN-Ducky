"""Per-project, per-file save snapshots under AppData.

Entry schema: ``backend/workspace/schemas/file_history_entry.schema.json``.
Version 2 adds attribution (who wrote the version: run, conversation, ducky,
model, tool). Version 1 entries have no ``schema_version`` key and read back with
empty attribution.

Writes reach this module through the pipeline's ``FileHistoryObserver``
(``frontend.ui_web.workspace_bootstrap``); nothing else should call
``record_write`` for project files.
"""

from __future__ import annotations

import hashlib
import json
import time
import warnings
from pathlib import Path
from typing import Any, Mapping

from frontend.settings import PanelSettings, default_app_data_dir
from frontend.ui_web.project_chats import project_slug

SCHEMA_VERSION = 2
# History is a browsing aid; durable revert lives in the changeset journal.
MAX_ENTRIES_PER_FILE = 100

SOURCE_AGENT = "agent"
SOURCE_USER = "user"
SOURCE_REVERT = "revert"

ATTRIBUTION_KEYS = (
    "run_id",
    "conv_id",
    "profile_id",
    "ducky_name",
    "model",
    "tool",
    "group_id",
    "coding_agent",
)


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


def _attribution(meta: Mapping[str, Any] | None) -> dict[str, str]:
    if not meta:
        return {}
    out: dict[str, str] = {}
    for key in ATTRIBUTION_KEYS:
        value = meta.get(key)
        if isinstance(value, str) and value:
            out[key] = value
    return out


def _write_snapshot(
    relative_path: str,
    content: str,
    project_root: str | None = None,
    source: str | None = None,
    meta: Mapping[str, Any] | None = None,
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
        "schema_version": SCHEMA_VERSION,
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
    payload.update(_attribution(meta))
    target = entries_dir / f"{entry_id}.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    _prune_entries(entries_dir)
    return entry_id


def snapshot_before_write(
    relative_path: str,
    old_content: str,
    project_root: str | None = None,
    source: str | None = None,
    meta: Mapping[str, Any] | None = None,
) -> None:
    """Store the previous on-disk content before overwriting (an unattributed restore point)."""
    _write_snapshot(relative_path, old_content, project_root, source=source, meta=meta)


def snapshot_editor_content(
    relative_path: str,
    content: str,
    project_root: str | None = None,
) -> dict[str, str]:
    """Store current editor content so the user can return after restoring an older version."""
    rel = _norm_path(relative_path)
    entry_id = _write_snapshot(rel, content, project_root)
    return {"id": entry_id, "path": rel}


def record_write(
    relative_path: str,
    before: str | None,
    after: str,
    project_root: str | None = None,
    *,
    writer: Mapping[str, Any] | None = None,
) -> dict[str, str | None]:
    """Record one applied write: a restore point for *before* and an attributed *after*.

    ``writer`` is the pipeline's attribution dict (``RunContext.as_writer``). A
    human save (``source == "user"``) keeps only the restore point, matching the
    editor's long-standing behaviour: the current buffer is the newest version.
    Returns ``{"before_id", "after_id"}`` (None when nothing was written).
    """
    rel = _norm_path(relative_path)
    meta = dict(writer or {})
    source = str(meta.get("source") or SOURCE_AGENT)
    before_id: str | None = None
    after_id: str | None = None
    if before and before != after:
        before_id = _write_snapshot(rel, before, project_root)
    if source != SOURCE_USER:
        after_id = _write_snapshot(rel, after, project_root, source=source, meta=meta)
    return {"before_id": before_id, "after_id": after_id}


def record_agent_write(
    relative_path: str,
    before: str,
    after: str,
    project_root: str | None = None,
) -> None:
    """Deprecated since 1.1.62; removed in 1.1.64. Writes go through ``ProjectWriter``,
    whose ``FileHistoryObserver`` calls :func:`record_write` with real attribution."""
    warnings.warn(
        "file_history.record_agent_write is deprecated; write through "
        "backend.workspace.runtime.get_writer() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    record_write(relative_path, before, after, project_root, writer={"source": SOURCE_AGENT})


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
        row: dict[str, Any] = {
            "schema_version": int(data.get("schema_version") or 1),
            "id": str(data.get("id") or path.stem),
            "path": str(data.get("path") or rel),
            "saved_at": int(data.get("saved_at") or 0),
            "bytes": int(data.get("bytes") or 0),
            "preview": str(data.get("preview") or ""),
            "content_hash": str(content_hash or ""),
            "source": str(src) if isinstance(src, str) and src else "",
        }
        for key in ATTRIBUTION_KEYS:
            value = data.get(key)
            row[key] = value if isinstance(value, str) else ""
        out.append(row)
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
