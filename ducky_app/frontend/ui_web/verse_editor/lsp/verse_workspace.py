"""Discover Verse multi-root workspace folders (matches VS Code / UEFN layout)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _verse_saved_root(project_name: str) -> Path:
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("USERPROFILE") or ""
    return Path(local) / "UnrealEditorFortnite" / "Saved" / "VerseProject" / project_name


def _parse_code_workspace(ws_path: Path) -> list[dict[str, str]]:
    try:
        data = json.loads(ws_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    folders = data.get("folders")
    if not isinstance(folders, list):
        return []
    out: list[dict[str, str]] = []
    for item in folders:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        name = str(item.get("name") or Path(path).name)
        try:
            resolved = str(Path(path).resolve())
        except OSError:
            resolved = path.replace("\\", "/")
        out.append({"name": name, "path": resolved})
    return out


def _saved_layout_folders(project_root: Path, saved: Path) -> list[dict[str, str]]:
    content = project_root / "Content"
    folders: list[dict[str, str]] = []
    if content.is_dir():
        folders.append({"name": project_root.name, "path": str(content.resolve())})
    assets = saved / f"{project_root.name}-Assets"
    if assets.is_dir():
        folders.append({"name": f"{project_root.name} (Assets)", "path": str(assets.resolve())})
    for sub, label in (
        ("vproject", "vproject - DO NOT MODIFY"),
        ("Verse", "/Verse.org"),
        ("UnrealEngine", "/UnrealEngine.com"),
        ("Fortnite", "/Fortnite.com"),
    ):
        p = saved / sub
        if p.is_dir():
            folders.append({"name": label, "path": str(p.resolve())})
    return folders


def _collect_watch_files(folder_paths: list[str]) -> list[str]:
    """vproject / vpackage files verse-lsp expects via didChangeWatchedFiles."""
    patterns = ("*.vproject", "*.vpackage")
    found: list[str] = []
    seen: set[str] = set()
    for raw in folder_paths:
        base = Path(raw)
        if not base.is_dir():
            if base.suffix.lower() in {".vproject", ".vpackage"} and base.is_file():
                key = str(base.resolve())
                if key not in seen:
                    seen.add(key)
                    found.append(key)
            continue
        for pattern in patterns:
            for path in base.rglob(pattern):
                if path.is_file():
                    key = str(path.resolve())
                    if key not in seen:
                        seen.add(key)
                        found.append(key)
    saved_parent = None
    for raw in folder_paths:
        p = Path(raw)
        if p.name.lower() == "vproject" and p.parent.name:
            saved_parent = p.parent
            break
    if saved_parent is not None:
        for pattern in patterns:
            for path in saved_parent.rglob(pattern):
                if path.is_file():
                    key = str(path.resolve())
                    if key not in seen:
                        seen.add(key)
                        found.append(key)
    return found


def discover_verse_workspace(project_root: str) -> dict[str, Any]:
    """
    Return workspace_folders [{name, path}] and watch_files [abs paths].

    Prefers *.code-workspace in the project root (UEFN-generated), then
  Saved/VerseProject/{name} layout under LOCALAPPDATA.
    """
    root = Path(project_root).resolve()
    folders: list[dict[str, str]] = []

    for ws_file in sorted(root.glob("*.code-workspace")):
        folders = _parse_code_workspace(ws_file)
        if folders:
            break

    if not folders:
        saved = _verse_saved_root(root.name)
        if saved.is_dir():
            folders = _saved_layout_folders(root, saved)

    if not folders:
        content = root / "Content"
        if content.is_dir():
            folders = [{"name": root.name, "path": str(content.resolve())}]
        else:
            folders = [{"name": root.name, "path": str(root)}]

    paths = [f["path"] for f in folders]
    watch_files = _collect_watch_files(paths)

    vproject = _verse_saved_root(root.name) / "vproject" / f"{root.name}.vproject"
    if vproject.is_file():
        vp = str(vproject.resolve())
        if vp not in watch_files:
            watch_files.insert(0, vp)

    return {"workspace_folders": folders, "watch_files": watch_files}
