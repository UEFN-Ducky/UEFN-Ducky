"""Discover Verse multi-root workspace folders (matches VS Code / UEFN layout)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# UEFN Core (and verse-lsp) expect one workspace root per digest package so the
# digest file sits at the folder root. Newer UEFN builds nest those under
# Digests/BuiltIn/ as a single "Built-in Digests" folder — expand that back out.
_BUILTIN_DIGEST_LABELS: dict[str, str] = {
    "verse": "/Verse.org",
    "unrealengine": "/UnrealEngine.com",
    "fortnite": "/Fortnite.com",
}


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


def _is_builtin_digests_root(path: Path, name: str = "") -> bool:
    """True for Digests/BuiltIn (new UEFN layout) or a folder named like Built-in Digests."""
    n = (name or path.name).strip().lower().replace("_", " ").replace("-", " ")
    if n in {"builtin", "built in digests", "built in"}:
        return True
    try:
        parts = [p.lower() for p in path.parts]
    except Exception:
        return False
    return len(parts) >= 2 and parts[-1] == "builtin" and parts[-2] == "digests"


def _fortnite_game_builtin_digests() -> Path | None:
    """Shared engine digests under VerseProject/FortniteGame (when island BuiltIn is missing)."""
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("USERPROFILE") or ""
    builtin = (
        Path(local)
        / "UnrealEditorFortnite"
        / "Saved"
        / "VerseProject"
        / "FortniteGame"
        / "Digests"
        / "BuiltIn"
    )
    return builtin if builtin.is_dir() else None


def _builtin_package_children(builtin: Path) -> list[dict[str, str]]:
    """Fortnite / UnrealEngine / Verse roots under a Digests/BuiltIn folder."""
    expanded: list[dict[str, str]] = []
    try:
        children = sorted(builtin.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    for child in children:
        if not child.is_dir():
            continue
        label = _BUILTIN_DIGEST_LABELS.get(child.name.lower())
        if not label:
            continue
        try:
            resolved = str(child.resolve())
        except OSError:
            resolved = str(child)
        expanded.append({"name": label, "path": resolved})
    return expanded


def _expand_builtin_digest_folders(folders: list[dict[str, str]]) -> list[dict[str, str]]:
    """Split Digests/BuiltIn into Fortnite / UnrealEngine / Verse roots (flat digests).

    If the island's BuiltIn folder is missing/empty (common before first Verse build),
    fall back to FortniteGame Digests/BuiltIn so UEFN Core still shows Unreal/Verse/Fortnite.
    """
    out: list[dict[str, str]] = []
    shared = _fortnite_game_builtin_digests()
    for folder in folders:
        path = Path(folder["path"])
        name = str(folder.get("name") or "")
        if not _is_builtin_digests_root(path, name):
            out.append(folder)
            continue
        expanded: list[dict[str, str]] = []
        if path.is_dir():
            expanded = _builtin_package_children(path)
        if not expanded and shared is not None and shared != path:
            expanded = _builtin_package_children(shared)
        if expanded:
            out.extend(expanded)
        # Drop empty/missing Built-in Digests roots — they only cause abs: escape errors.
    return out


def _digest_package_dir(saved: Path, package: str) -> Path | None:
    """Resolve Fortnite/Verse/UnrealEngine under Digests/BuiltIn (new) or saved root (old)."""
    builtin = saved / "Digests" / "BuiltIn" / package
    if builtin.is_dir():
        return builtin
    legacy = saved / package
    return legacy if legacy.is_dir() else None


def _assets_digest_dir(saved: Path, project_name: str) -> Path | None:
    nested = saved / "Digests" / f"{project_name}-Assets"
    if nested.is_dir():
        return nested
    legacy = saved / f"{project_name}-Assets"
    return legacy if legacy.is_dir() else None


def _saved_layout_folders(project_root: Path, saved: Path) -> list[dict[str, str]]:
    content = project_root / "Content"
    folders: list[dict[str, str]] = []
    if content.is_dir():
        folders.append({"name": project_root.name, "path": str(content.resolve())})
    assets = _assets_digest_dir(saved, project_root.name)
    if assets is not None:
        folders.append({"name": f"{project_root.name} (Assets)", "path": str(assets.resolve())})
    vproject = saved / "vproject"
    if vproject.is_dir():
        folders.append({"name": "vproject - DO NOT MODIFY", "path": str(vproject.resolve())})
    packages: list[dict[str, str]] = []
    for sub, label in (
        ("Verse", "/Verse.org"),
        ("UnrealEngine", "/UnrealEngine.com"),
        ("Fortnite", "/Fortnite.com"),
    ):
        p = _digest_package_dir(saved, sub)
        if p is not None:
            packages.append({"name": label, "path": str(p.resolve())})
    if not packages:
        shared = _fortnite_game_builtin_digests()
        if shared is not None:
            packages = _builtin_package_children(shared)
    folders.extend(packages)
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
        # New layout: .../VerseProject/<proj>/Digests/BuiltIn/Fortnite → saved is grandparent×2
        if p.name.lower() in _BUILTIN_DIGEST_LABELS and p.parent.name.lower() == "builtin":
            saved_parent = p.parent.parent.parent
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

    Digests/BuiltIn (new UEFN) is expanded into Fortnite / UnrealEngine / Verse
    roots so UEFN Core can list *.digest.verse as top-level read-only files.
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

    folders = _expand_builtin_digest_folders(folders)

    paths = [f["path"] for f in folders]
    watch_files = _collect_watch_files(paths)

    vproject = _verse_saved_root(root.name) / "vproject" / f"{root.name}.vproject"
    if vproject.is_file():
        vp = str(vproject.resolve())
        if vp not in watch_files:
            watch_files.insert(0, vp)

    return {"workspace_folders": folders, "watch_files": watch_files}
