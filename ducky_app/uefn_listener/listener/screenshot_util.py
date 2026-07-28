"""Pure screenshot path / capture-state helpers (no Unreal import).

Used by the listener registry and unit-tested without the editor.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from typing import Any

CAPTURE_TIMEOUT_SEC = 25.0
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def unique_screenshot_filename(filename: str = "") -> str:
    """Return a unique PNG basename so concurrent captures do not collide."""
    raw = (filename or "").strip()
    base = os.path.basename(raw) if raw else ""
    if base.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        stem, ext = os.path.splitext(base)
    else:
        stem = base or "ducky_capture"
        ext = ".png"
    stem = _SAFE_NAME_RE.sub("_", stem).strip("._")[:48] or "ducky_capture"
    return f"{stem}_{int(time.time())}_{uuid.uuid4().hex[:8]}{ext.lower()}"


def list_screenshot_candidate_paths(filename: str, screenshot_roots: list[str]) -> list[str]:
    """Bounded candidates: direct file + one subdirectory level under each root."""
    name = os.path.basename(filename or "")
    if not name:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for root in screenshot_roots:
        if not root:
            continue
        direct = os.path.join(root, name)
        if direct not in seen:
            seen.add(direct)
            out.append(direct)
        if not os.path.isdir(root):
            continue
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        for entry in entries:
            sub = os.path.join(root, entry)
            if not os.path.isdir(sub):
                continue
            path = os.path.join(sub, name)
            if path not in seen:
                seen.add(path)
                out.append(path)
    return out


def resolve_screenshot_path(
    filename: str,
    screenshot_roots: list[str],
    *,
    started_at: float = 0.0,
    min_size: int = 1,
) -> str:
    """Locate a freshly written PNG under Saved/Screenshots (bounded, no deep walk)."""
    name = os.path.basename(filename or "")
    if not name:
        return ""
    if os.path.isabs(filename) and os.path.isfile(filename):
        try:
            if os.path.getsize(filename) >= min_size:
                return filename
        except OSError:
            return ""
        return ""

    cutoff = (started_at - 2.0) if started_at > 0 else (time.time() - 120.0)
    best = ""
    best_mtime = 0.0
    for path in list_screenshot_candidate_paths(name, screenshot_roots):
        if not os.path.isfile(path):
            continue
        try:
            st = os.stat(path)
            if st.st_size < min_size:
                continue
            mtime = st.st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        if mtime >= best_mtime:
            best = path
            best_mtime = mtime
    return best


class CaptureStore:
    """In-memory pending AutomationEditorTask captures (listener process)."""

    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}

    def put(self, capture_id: str, record: dict[str, Any]) -> None:
        self._items[capture_id] = record

    def get(self, capture_id: str) -> dict[str, Any] | None:
        return self._items.get(capture_id)

    def pop(self, capture_id: str) -> dict[str, Any] | None:
        return self._items.pop(capture_id, None)

    def discard(self, capture_id: str) -> None:
        self._items.pop(capture_id, None)

    def items(self) -> list[tuple[str, dict[str, Any]]]:
        return list(self._items.items())

    def prune_expired(self, *, now: float | None = None, max_age: float = CAPTURE_TIMEOUT_SEC * 2) -> None:
        ts = time.time() if now is None else now
        stale = [
            cid
            for cid, rec in self._items.items()
            if float(rec.get("started_at") or 0) > 0 and (ts - float(rec["started_at"])) > max_age
        ]
        for cid in stale:
            self._items.pop(cid, None)
