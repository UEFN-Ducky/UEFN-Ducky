"""Project path rules shared by every write path.

Pure helpers: no I/O beyond ``os.path`` normalisation, no frontend imports.
``backend.tools.core.system`` and ``backend.tools.verse.verse_digests`` re-export
the guard functions so existing importers keep working.
"""

from __future__ import annotations

import difflib
import hashlib
import os

WRITE_OUTSIDE_CONTENT_FORBIDDEN = (
    "workspace_write_file may only write under the UEFN project's Content/ or "
    ".ducky/ folders. Never touch UEFN core files, digests, Saved/, Intermediate/, "
    "or the project root. Never write .py/.pyc into the island (Epic rejects "
    "the upload with ContainsPythonData). Scratch files belong in "
    "%LOCALAPPDATA%/UEFN-Ducky/ (or OS temp). Allowed: Content/** (Verse, assets) "
    "and .ducky/** (tests, tasks) — never Python."
)

WRITE_PYTHON_IN_PROJECT_FORBIDDEN = (
    "Never write extra .py/.pyc into a UEFN project. Ducky auto-manages "
    "Content/Python/init_unreal.py (listener boot) — never delete that file. "
    "Scratch → %LOCALAPPDATA%/UEFN-Ducky/. execute_python is in-memory only."
)

DIGEST_WRITE_FORBIDDEN = (
    "UEFN digests are READ-ONLY — UEFN auto-edits them on Verse build. Never write, "
    "edit, delete, rename, or patch any *.digest.verse. Run workspace_compile_verse "
    "then search_verse_digest / get_verse_api / list_verse_types. Write project Verse "
    "under Content/Verse/ only."
)

_WRITE_BLOCKED_ANCESTORS = frozenset({"Saved", "Intermediate", "DerivedDataCache"})
_WRITE_ALLOWED_ANCESTORS = frozenset({"Content", ".ducky"})
_WRITE_BLOCKED_SUFFIXES = (".py", ".pyc")

# Hash width shared with the editor's file history so ids line up across stores.
HASH_HEX_CHARS = 16


def normalize_rel(path: str) -> str:
    """Forward slashes, no leading ``./`` or ``/``, no surrounding whitespace."""
    p = (path or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:HASH_HEX_CHARS]


def rel_from_root(full_path: str, root: str) -> str | None:
    """``full_path`` relative to ``root`` with forward slashes, or None if outside."""
    try:
        full = os.path.realpath(os.path.abspath(full_path))
        base = os.path.realpath(os.path.abspath(root))
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


def require_not_digest_path(path: str) -> None:
    """Raise if *path* names a UEFN digest — digests must never be mutated."""
    if is_uefn_digest_path(path):
        raise ValueError(DIGEST_WRITE_FORBIDDEN)


def require_writable_project_path(file_path: str) -> None:
    """Refuse writes outside Content/** and .ducky/** of a UEFN project.

    Digests are blocked separately by require_not_digest_path. This guard stops
    the rest: Saved/, Intermediate/, project-root junk, UEFN core files, and
    any .py/.pyc (Epic ContainsPythonData).
    """
    lowered = os.path.normpath(os.path.abspath(file_path or "")).replace("\\", "/").lower()
    if lowered.endswith(_WRITE_BLOCKED_SUFFIXES):
        raise ValueError(WRITE_PYTHON_IN_PROJECT_FORBIDDEN)
    parts = os.path.normpath(os.path.abspath(file_path or "")).replace("\\", "/").split("/")
    names = [p for p in parts if p and p != "."]
    for i, name in enumerate(names):
        if name in _WRITE_BLOCKED_ANCESTORS:
            raise ValueError(WRITE_OUTSIDE_CONTENT_FORBIDDEN)
        if name in _WRITE_ALLOWED_ANCESTORS and i < len(names) - 1:
            return
    raise ValueError(WRITE_OUTSIDE_CONTENT_FORBIDDEN)
