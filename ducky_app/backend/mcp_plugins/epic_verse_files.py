"""Delete and move .verse files through Epic's VerseToolset while UEFN has the island open.

UEFN's Verse build keeps its own list of the island's .verse files and follows file
events to update it. A folder renamed or moved in one step on disk (the undo trash moving
a deleted folder out of Content, too) reports only the folder: its files stay on the list
where they were, and every later build fails with Script error 3000 "Error getting text
contents of snippet …"; files that arrive inside a moved folder are never compiled.
``VerseToolset.Delete`` / ``Move`` update the list, so Ducky's file operations use them
whenever Epic MCP is online for the panel's island and the toolset takes the entry whole
(a .verse file, or a folder holding only .verse files). Everything else stays a disk
step, file by file for a folder that holds .verse files
(``frontend.ui_web.project_files._move_entry``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

VERSE_TOOLSET = "VerseToolset.VerseToolset"  # 43.00 name; ValkyrieToolset.VerseToolset answers "not found"
_LIST_TIMEOUT_SEC = 10.0
_CHANGE_TIMEOUT_SEC = 60.0


def _call(tool: str, arguments: dict[str, Any], timeout: float) -> str:
    """Nested ``unreal__call_tool`` on the VerseToolset. Patched in tests."""
    from backend.mcp_plugins.client_pool import get_plugin_pool

    pool = get_plugin_pool()
    args = {"toolset_name": VERSE_TOOLSET, "tool_name": tool, "arguments": arguments}
    return pool.run_sync(pool.call_tool("unreal__call_tool", args), timeout=timeout)


def _is_verse(name: str) -> bool:
    return name.lower().endswith(".verse")


def holds_verse(entry: Path) -> bool:
    """A .verse file, or a folder with a .verse file anywhere under it."""
    if entry.is_file():
        return _is_verse(entry.name)
    return any(any(_is_verse(f) for f in files) for _dir, _dirs, files in os.walk(entry))


def _toolset_takes(entry: Path) -> bool:
    """What Delete / Move take whole: a .verse file, or a folder holding .verse files only."""
    if entry.is_file():
        return _is_verse(entry.name)
    found = False
    for _dir, _dirs, files in os.walk(entry):
        for name in files:
            if not _is_verse(name):
                return False
            found = True
    return found


def _return_value(text: str) -> Any:
    # The pool may append a "[Ducky: pressed …]" note after the JSON.
    try:
        data, _end = json.JSONDecoder().raw_decode(str(text or "").lstrip())
    except ValueError:
        return None
    return data.get("returnValue") if isinstance(data, dict) else None


def island_package(project_root: Path) -> str:
    """The island's Verse package (``/MyIsland``) when UEFN has this island open, else ''."""
    from backend.mcp_plugins.epic import probe_epic_mcp
    from frontend.project_kind import has_uefnproject, is_island_identity

    if not has_uefnproject(project_root):
        return ""
    if not probe_epic_mcp(project_root=str(project_root)).get("epic_mcp_online"):
        return ""
    try:
        listing = _return_value(_call("ListFiles", {"path": "", "bRecursive": False}, _LIST_TIMEOUT_SEC))
    except Exception:
        return ""
    for row in listing if isinstance(listing, list) else []:
        name = str((row or {}).get("name") or "") if isinstance(row, dict) else ""
        # The island's own package; read-only digests are named like "/Verse.org (Verse)".
        if name.startswith("/") and " (" not in name:
            return name if is_island_identity(project_root, name.strip("/")) else ""
    return ""


def verse_path(package: str, project_root: Path, entry: Path) -> str:
    """``<root>/Content/Verse/A/b.verse`` → ``/MyIsland/Verse/A/b.verse``."""
    rel = entry.resolve().relative_to((project_root / "Content").resolve())
    return f"{package}/{rel.as_posix()}"


def delete_package(project_root: Path, entry: Path) -> str:
    """Package to delete ``entry`` through, '' when it is a disk delete."""
    return island_package(project_root) if _toolset_takes(entry) else ""


def delete(package: str, project_root: Path, entry: Path) -> bool:
    """``VerseToolset.Delete``. True when the entry is gone from disk."""
    try:
        _call(
            "Delete",
            {"path": verse_path(package, project_root, entry), "bRecursive": entry.is_dir()},
            _CHANGE_TIMEOUT_SEC,
        )
    except Exception:
        pass
    return not entry.exists()


def move(project_root: Path, source: Path, dest: Path) -> bool:
    """``VerseToolset.Move`` when the toolset takes the entry. True when it landed at ``dest``.

    False leaves both paths as they were, for the disk step.
    """
    if source.is_file() and not _is_verse(dest.name):
        return False
    package = delete_package(project_root, source)
    if not package:
        return False
    try:
        _call(
            "Move",
            {
                "sourcePath": verse_path(package, project_root, source),
                "destPath": verse_path(package, project_root, dest),
            },
            _CHANGE_TIMEOUT_SEC,
        )
    except Exception:
        pass
    if dest.exists() and not source.exists():
        return True
    if dest.exists():
        raise ValueError(
            f"UEFN stopped partway moving {source.name}: both {source} and {dest} exist. "
            "Check both before trying again."
        )
    return False
