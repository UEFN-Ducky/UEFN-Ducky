"""Read/write project files with content cache for diff baselines.

Panel saves go through the shared write pipeline (``backend.workspace``), which
owns atomic replace, history snapshots, the change journal and policy. This
module keeps the panel-only rules (Content-only, editable text, not locked) and
the content cache the follow-code diff uses as its baseline.
"""

from __future__ import annotations

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
    """Save from the panel editor: panel rules, then the shared write pipeline."""
    pf._require_writable_content_path(relative_path)  # noqa: SLF001
    pf._require_not_locked(relative_path)  # noqa: SLF001
    pf._require_not_digest(relative_path)  # noqa: SLF001 — never mutate UEFN digests
    target = pf._resolve_relative(relative_path)  # noqa: SLF001 — shared path rules
    pf._require_under_content(target, relative_path)  # noqa: SLF001
    if not pf.is_editable_text_file(relative_path):
        raise ValueError(f"File type cannot be saved in panel: {relative_path}")
    rel = str(target.relative_to(pf._project_root().resolve())).replace("\\", "/")  # noqa: SLF001

    from backend.workspace.runtime import get_writer

    result = get_writer().write_text(rel, content, tool="panel_save")
    _cache.set(rel, content)
    return {"path": rel, "bytes_written": result.bytes_written}


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
