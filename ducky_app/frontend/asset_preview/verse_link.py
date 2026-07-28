"""Resolve compiled Verse uassets to their ``.verse`` source files (disk-only)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

_CLASS_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _class_pattern(stem: str) -> re.Pattern[str]:
    cached = _CLASS_RE_CACHE.get(stem)
    if cached is None:
        cached = re.compile(rf"(?i)\b{re.escape(stem)}\s*:=\s*class\b")
        _CLASS_RE_CACHE[stem] = cached
    return cached


def _verse_search_dirs(project_root: Path) -> list[Path]:
    roots: list[Path] = []
    for name in ("Verse", "Content/Verse"):
        p = project_root / name.replace("/", os.sep)
        if p.is_dir():
            roots.append(p)
    content = project_root / "Content"
    if content.is_dir():
        for child in content.iterdir():
            if child.is_dir() and child.name.lower() == "verse":
                roots.append(child)
    return roots


def _relative_project_path(project_root: Path, absolute: str) -> str:
    try:
        return str(Path(absolute).resolve().relative_to(project_root.resolve())).replace("\\", "/")
    except ValueError:
        return absolute.replace("\\", "/")


def stem_from_relative_path(relative_path: str) -> Optional[str]:
    rel = (relative_path or "").replace("\\", "/")
    if "/_verse/" not in rel.lower() and not rel.lower().startswith("content/_verse"):
        return None
    name = Path(rel).stem
    if name.lower().startswith("verse-"):
        return name.split("-", 1)[1]
    if name.lower().startswith("verse_"):
        return name.split("_", 1)[1]
    return name


def find_verse_source(project_root: str, *, relative_path: str = "", verse_class_stem: str = "") -> Optional[str]:
    """Return project-relative ``Verse/.../*.verse`` path if found."""
    root = Path(project_root).resolve()
    stems: list[str] = []
    if verse_class_stem:
        stems.append(verse_class_stem.strip())
    from_path = stem_from_relative_path(relative_path)
    if from_path and from_path not in stems:
        stems.append(from_path)

    for stem in stems:
        if not stem:
            continue
        pattern = _class_pattern(stem)
        for verse_root in _verse_search_dirs(root):
            for dirpath, _dn, filenames in os.walk(verse_root):
                for fn in filenames:
                    if not fn.endswith(".verse"):
                        continue
                    fp = os.path.join(dirpath, fn)
                    try:
                        text = Path(fp).read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    if pattern.search(text):
                        return _relative_project_path(root, fp)
    return None
