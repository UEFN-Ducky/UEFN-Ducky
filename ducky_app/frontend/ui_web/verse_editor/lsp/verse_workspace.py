"""Discover Verse multi-root workspace folders (matches VS Code / UEFN layout)."""

from __future__ import annotations

import json
import os
import shutil
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

# Built-in digest packages every island .vproject depends on. When UEFN never wrote the
# island's Digests/BuiltIn (interrupted first open, editor killed mid-save) the .vproject
# still points there, and verse-lsp resolves packages from the .vproject — NOT from the
# workspace folders — so it loads zero digests and every `using { /Fortnite.com/... }`,
# `creative_device` and `Print` becomes an error. Ducky hands verse-lsp a shadow copy of
# the .vproject with those packages pointed at FortniteGame's shared digests instead.
_SHARED_DIGEST_PACKAGES = frozenset({"Verse", "UnrealEngine", "Fortnite"})
_SHADOW_VPROJECT_DIRNAME = "vproject-shadow"


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
    builtin = _verse_saved_root("FortniteGame") / "Digests" / "BuiltIn"
    return builtin if builtin.is_dir() else None


def _has_digest(path: Path) -> bool:
    """A package dir verse-lsp can actually load: exists and holds a *.digest.verse."""
    try:
        return path.is_dir() and any(path.glob("*.digest.verse"))
    except OSError:
        return False


def _shared_digest_package_dir(package: str) -> Path | None:
    """FortniteGame's copy of a built-in package (Digests/BuiltIn/<pkg>, else legacy <pkg>)."""
    shared = _digest_package_dir(_verse_saved_root("FortniteGame"), package)
    if shared is not None and _has_digest(shared):
        return shared
    return None


def _shadow_vproject_root() -> Path:
    from frontend.settings import default_app_data_dir

    return default_app_data_dir() / "verse-lsp" / _SHADOW_VPROJECT_DIRNAME


def _rewrite_missing_builtin_packages(vproject_text: str) -> tuple[str | None, list[str]]:
    """Point built-in packages whose dirPath holds no digest at FortniteGame's shared copy.

    Returns (rewritten JSON, names of packages changed); (None, []) when every built-in
    package already resolves or nothing shared exists to substitute.
    """
    data = json.loads(vproject_text)
    packages = data.get("packages") if isinstance(data, dict) else None
    if not isinstance(packages, list):
        return None, []
    changed: list[str] = []
    for pkg in packages:
        desc = pkg.get("desc") if isinstance(pkg, dict) else None
        if not isinstance(desc, dict):
            continue
        name = str(desc.get("name") or "")
        if name not in _SHARED_DIGEST_PACKAGES:
            continue
        dir_path = str(desc.get("dirPath") or "")
        if dir_path and _has_digest(Path(dir_path)):
            continue
        shared = _shared_digest_package_dir(name)
        if shared is None:
            continue
        desc["dirPath"] = shared.resolve().as_posix()
        changed.append(name)
    if not changed:
        return None, []
    return json.dumps(data, indent=4), changed


def _prune_shadow_vproject(project_name: str) -> None:
    """Drop a stale shadow once UEFN has written the island's own digests."""
    shadow_project = _shadow_vproject_root() / project_name
    if not shadow_project.is_dir():
        return
    try:
        shutil.rmtree(shadow_project)
    except OSError:
        pass


def shadow_vproject_for(project_root: Path, real_vproject: Path) -> Path | None:
    """Ducky-owned .vproject to hand verse-lsp instead of the island's, or None to use the real one.

    Written under Ducky's AppData (never inside UEFN's Saved tree) only while the island file
    references built-in digest folders that hold no digest and FortniteGame has them. It is
    regenerated whenever the island file changes and removed once UEFN writes the island
    digests, which flips the workspace fingerprint back to the real layout.
    """
    try:
        text = real_vproject.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        rewritten, _changed = _rewrite_missing_builtin_packages(text)
    except (TypeError, ValueError):
        return None
    if rewritten is None:
        _prune_shadow_vproject(project_root.name)
        return None
    shadow_dir = _shadow_vproject_root() / project_root.name / "vproject"
    shadow = shadow_dir / real_vproject.name
    try:
        shadow_dir.mkdir(parents=True, exist_ok=True)
        current = shadow.read_text(encoding="utf-8") if shadow.is_file() else None
        if current != rewritten:
            shadow.write_text(rewritten, encoding="utf-8")
    except OSError:
        return None
    return shadow


def _is_vproject_folder(folder: dict[str, str]) -> bool:
    name = str(folder.get("name") or "").strip().lower()
    path = Path(str(folder.get("path") or ""))
    return name.startswith("vproject") or path.name.lower() == "vproject"


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

    When the island .vproject points its built-in packages at digests UEFN never
    wrote, the vproject root and watch file are swapped for a Ducky-owned shadow
    copy that resolves them from FortniteGame (``vproject_shadow`` holds its path,
    "" when the real file is in use).
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

    real_vproject = _verse_saved_root(root.name) / "vproject" / f"{root.name}.vproject"
    shadow_vproject: Path | None = None
    if real_vproject.is_file():
        shadow_vproject = shadow_vproject_for(root, real_vproject)
    if shadow_vproject is not None:
        # verse-lsp must see ONLY the shadow: it picks up any .vproject inside a workspace
        # folder, and the real one (dead digest paths) wins if both are visible.
        shadow_dir = str(shadow_vproject.parent)
        replaced = False
        for folder in folders:
            if _is_vproject_folder(folder):
                folder["path"] = shadow_dir
                replaced = True
        if not replaced:
            folders.append({"name": "vproject (read-only)", "path": shadow_dir})

    paths = [f["path"] for f in folders]
    watch_files = _collect_watch_files(paths)

    if shadow_vproject is not None:
        real_key = str(real_vproject.resolve())
        watch_files = [w for w in watch_files if w != real_key]
        sv = str(shadow_vproject.resolve())
        if sv not in watch_files:
            watch_files.insert(0, sv)
    elif real_vproject.is_file():
        vp = str(real_vproject.resolve())
        if vp not in watch_files:
            watch_files.insert(0, vp)

    return {
        "workspace_folders": folders,
        "watch_files": watch_files,
        "vproject_shadow": str(shadow_vproject) if shadow_vproject is not None else "",
    }


def workspace_folder_fingerprint(project_root: str) -> str:
    """Stable id of the Verse workspace roots verse-lsp was (or will be) initialized with.

    BuiltIn digest packages appear only after a Verse build. A long-lived verse-lsp that
    started before that still has Content-only folders and reports Fortnite.com as unknown.
    """
    if not (project_root or "").strip():
        return ""
    try:
        ws = discover_verse_workspace(project_root)
    except Exception:
        return ""
    folders = ws.get("workspace_folders") or []
    parts: list[str] = []
    for folder in folders:
        if not isinstance(folder, dict):
            continue
        parts.append(f"{folder.get('name') or ''}\t{folder.get('path') or ''}")
    return "\n".join(sorted(parts))
