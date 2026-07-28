"""Read/write project files with content cache for diff baselines."""

from __future__ import annotations

import os
import tempfile
from threading import Lock

from frontend.ui_web import project_files as pf
from frontend.ui_web.verse_editor import file_history


class ContentCache:
    """Single source of truth for file content used in agent diff visualization."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._by_path: dict[str, str] = {}

    def get(self, relative_path: str) -> str | None:
        norm = _norm_path(relative_path)
        with self._lock:
            return self._by_path.get(norm)

    def set(self, relative_path: str, content: str) -> None:
        norm = _norm_path(relative_path)
        with self._lock:
            self._by_path[norm] = content

    def clear(self) -> None:
        with self._lock:
            self._by_path.clear()


_cache = ContentCache()


def _norm_path(relative_path: str) -> str:
    return (relative_path or "").strip().replace("\\", "/")


def get_cached(relative_path: str) -> str | None:
    return _cache.get(relative_path)


def read_file(relative_path: str) -> dict[str, str]:
    result = pf.read_project_file(relative_path)
    _cache.set(result["path"], result["content"])
    return result


def write_file(relative_path: str, content: str) -> dict[str, object]:
    """Atomic write under project root; updates cache."""
    pf._require_writable_content_path(relative_path)  # noqa: SLF001
    pf._require_not_locked(relative_path)  # noqa: SLF001
    target = pf._resolve_relative(relative_path)  # noqa: SLF001 — shared path rules
    pf._require_under_content(target, relative_path)  # noqa: SLF001
    if not pf.is_editable_text_file(relative_path):
        raise ValueError(f"File type cannot be saved in panel: {relative_path}")
    if not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    rel = str(target.relative_to(pf._project_root().resolve())).replace("\\", "/")  # noqa: SLF001
    project_root = str(pf._project_root().resolve())  # noqa: SLF001
    if target.is_file():
        try:
            old_content = target.read_text(encoding="utf-8")
            if old_content != content:
                file_history.snapshot_before_write(rel, old_content, project_root)
        except OSError:
            pass
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent),
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    _cache.set(rel, content)
    return {"path": rel, "bytes_written": len(content.encode("utf-8"))}


def seed_cache(relative_path: str, content: str) -> None:
    _cache.set(relative_path, content)


def record_external_file_change(
    relative_path: str,
    previous_content: str,
    new_content: str,
    project_root: str | None = None,
) -> dict[str, object]:
    """Snapshot content replaced by an out-of-band disk write; refresh agent diff cache."""
    rel = _norm_path(relative_path)
    snapshotted = previous_content != new_content
    if snapshotted:
        file_history.snapshot_before_write(rel, previous_content, project_root)
    _cache.set(rel, new_content)
    return {"ok": True, "snapshotted": snapshotted}
