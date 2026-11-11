"""Project path rules shared by every write path.

Pure helpers: no I/O beyond ``os.path`` normalisation, no frontend imports.

A write may go anywhere inside its project, any file type, as Claude Code writes in its
folder: no island-only rules (Content/ and .ducky/ only, no .py, no digests, no
Saved/ or Intermediate/) and no folder-project ones (.git internals). The owner had them
all removed (Nov 11 2026); only a path outside the project is refused.
"""

from __future__ import annotations

import difflib
import hashlib
import os

WRITE_OUTSIDE_PROJECT_FORBIDDEN = (
    "That path is outside the project. Writes go anywhere inside the open project (or a "
    "folder the step lends); open the other project first to write there."
)

# Hash width shared with the editor's file history so ids line up across stores.
HASH_HEX_CHARS = 16


def real_path(path: str) -> str:
    """``os.path.realpath`` without the ``\\\\?\\`` prefix Windows adds when the file appears
    mid-call (another thread creating it): that form never matches a plain root."""
    full = os.path.realpath(os.path.abspath(path))
    if full.startswith("\\\\?\\") and full[5:6] == ":":
        return full[4:]
    return full


def island_root(path: str) -> str:
    """Island folder. Writer / MCP often hand ``…/<Project>/Content``; the panel hands ``…/<Project>``."""
    full = real_path(path or "")
    if os.path.basename(full).lower() == "content":
        parent = os.path.dirname(full)
        if parent:
            return parent
    return full


def same_project_roots(a: str, b: str) -> bool:
    """True when *a* and *b* are the same island (Content vs project folder counts)."""
    if not (a or "").strip() or not (b or "").strip():
        return False
    return island_root(a) == island_root(b)


def normalize_rel(path: str) -> str:
    """Forward slashes, no leading ``./`` or ``/``, no surrounding whitespace."""
    p = (path or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:HASH_HEX_CHARS]


_LONG_PREFIX = "\\\\?\\"
_LONG_UNC_PREFIX = "\\\\?\\UNC\\"
_DELETED_DIR = "\\$extend\\$deleted\\"


def real_path(path: str) -> str:
    """``os.path.realpath`` that holds while another thread creates or replaces the file.

    On Windows, realpath of a file that is being replaced can name the old copy
    (``C:\\$Extend\\$Deleted\\…``) or keep the ``\\\\?\\`` prefix it added; the path then
    looks outside its project. That result is resolved again from the parent folder.
    """
    full = os.path.abspath(path)
    got = os.path.realpath(full)
    if os.name != "nt":
        return got
    if not full.startswith(_LONG_PREFIX):
        if got.startswith(_LONG_UNC_PREFIX):
            got = "\\\\" + got[len(_LONG_UNC_PREFIX):]
        elif got.startswith(_LONG_PREFIX):
            got = got[len(_LONG_PREFIX):]
    if _DELETED_DIR in os.path.normcase(got):
        parent, name = os.path.split(full)
        if name and parent != full:
            got = os.path.join(real_path(parent), name)
    return got


def rel_from_root(full_path: str, root: str) -> str | None:
    """``full_path`` relative to ``root`` with forward slashes, or None if outside."""
    try:
        full = real_path(full_path)
        base = real_path(root)
        if os.path.commonpath([full, base]) != base:
            return None
        return os.path.relpath(full, base).replace("\\", "/")
    except ValueError:
        return None


def line_delta(before: str, after: str) -> tuple[int, int]:
    """(lines_added, lines_removed) between two texts."""
    matcher = difflib.SequenceMatcher(None, before.splitlines(), after.splitlines())
    added = removed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "insert":
            added += j2 - j1
        elif tag == "delete":
            removed += i2 - i1
        elif tag == "replace":
            removed += i2 - i1
            added += j2 - j1
    return added, removed


def is_uefn_digest_path(path: str) -> bool:
    """True for Fortnite/Verse/UnrealEngine/Assets *.digest.verse (any path)."""
    name = os.path.basename((path or "").replace("\\", "/")).lower()
    if not name:
        return False
    return name.endswith(".digest.verse") or (name.endswith(".verse") and "digest" in name)


def require_inside_project(file_path: str, root: str) -> None:
    """Anywhere under *root*, any file type (an island's or a folder project's alike)."""
    rel = rel_from_root(file_path, root)
    if rel is None or rel in ("", "."):
        raise ValueError(WRITE_OUTSIDE_PROJECT_FORBIDDEN)
    parts = [p for p in rel.split("/") if p and p != "."]
    if not parts or parts[0] == "..":
        raise ValueError(WRITE_OUTSIDE_PROJECT_FORBIDDEN)
