"""Every project Ducky knows: the ones the person opened plus every UEFN island on disk.

Islands are found without anyone adding them: each child of ``Documents/Fortnite
Projects`` (UEFN's default home) and of any folder that holds an island Ducky already
opened, when it has a ``*.uefnproject``. Agents' file tools reach every known project,
the Projects window lists them, and each island gets the listener at boot.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from frontend.project_kind import UEFN, has_uefnproject, project_flavor, project_kind, read_uefnproject

FOLDERID_DOCUMENTS = "FDD39AD0-238F-46AF-ADB4-6C85480369C7"
UEFN_PROJECTS_DIR = "Fortnite Projects"
# ponytail: discovery is a few dir listings; 30 s keeps file-tool path checks cheap.
_TTL_S = 30.0

_lock = threading.Lock()
_cache: tuple[float, list[str]] | None = None


def documents_dir() -> Path:
    """The person's Documents folder (OneDrive-redirected too), else ``~/Documents``.
    ``UEFN_DUCKY_DOCUMENTS_DIR`` overrides it (tests: never the real islands)."""
    override = os.environ.get("UEFN_DUCKY_DOCUMENTS_DIR", "").strip()
    if override:
        return Path(override)
    try:
        from frontend.app_paths import windows_known_folder

        found = windows_known_folder(FOLDERID_DOCUMENTS)
    except Exception:
        found = ""
    return Path(found) if found else Path.home() / "Documents"


def _norm(path: str | Path) -> str:
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(path)


def _islands_in(folder: Path) -> list[str]:
    try:
        children = list(folder.iterdir())
    except OSError:
        return []
    out: list[str] = []
    for child in children:
        try:
            if child.is_dir() and has_uefnproject(child):
                out.append(_norm(child))
        except OSError:
            continue
    return out


def discover_islands(saved: list[str] | None = None) -> list[str]:
    """Islands under ``Documents/Fortnite Projects`` and beside any saved island."""
    if saved is None:
        from frontend.ui_web.recent_projects import load_saved_projects

        saved = [p for p, _ in load_saved_projects()]
    parents: list[Path] = [documents_dir() / UEFN_PROJECTS_DIR]
    for path in saved:
        if has_uefnproject(path):
            parents.append(Path(path).parent)
    seen_parents: set[str] = set()
    out: list[str] = []
    for parent in parents:
        key = os.path.normcase(_norm(parent))
        if key in seen_parents:
            continue
        seen_parents.add(key)
        for island in _islands_in(parent):
            if island not in out:
                out.append(island)
    return out


def known_project_roots(*, rescan: bool = False) -> list[str]:
    """Saved projects plus discovered islands: the folders agents' file tools may reach."""
    global _cache
    now = time.monotonic()
    with _lock:
        if not rescan and _cache and now - _cache[0] < _TTL_S:
            return list(_cache[1])
    from frontend.ui_web.recent_projects import load_removed_projects, load_saved_projects

    saved = [p for p, _ in load_saved_projects()]
    removed = {os.path.normcase(p) for p in load_removed_projects()}
    found = [p for p in discover_islands(saved) if os.path.normcase(p) not in removed]
    roots = list(dict.fromkeys([*saved, *found]))
    with _lock:
        _cache = (now, roots)
    return list(roots)


def forget_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def thumbnail_path(root: str) -> str:
    """The island's key art (``keyArt`` in its ``.uefnproject``), or "" without one."""
    rel = str(read_uefnproject(root).get("keyArt") or "").strip()
    if not rel:
        return ""
    base = Path(root).resolve()
    try:
        full = (base / rel).resolve()
        full.relative_to(base)
    except (OSError, ValueError):
        return ""
    return str(full) if full.is_file() else ""


def _plugin_rows() -> tuple[list[dict], list[dict]]:
    """Tabs and rows plugins add through ``contributes['project.sources']``."""
    try:
        from backend.uefn_plugins.project_sources import list_plugin_projects

        return list_plugin_projects()
    except Exception:
        return [], []


def list_known_projects(*, rescan: bool = False) -> dict:
    """The Projects window: ``{sources, projects}``. Each project row has path, name, slug,
    kind, flavor, mount, source ('uefn' / 'other' / a plugin source id), last_opened,
    has_thumbnail, active and listener_ready."""
    from frontend.settings import PanelSettings
    from frontend.ui_web.chats.store import project_display_name, project_slug
    from frontend.ui_web.project_switch import _mount_for, normalize_project_path
    from frontend.ui_web.recent_projects import load_saved_projects

    opened = dict(load_saved_projects())
    current = normalize_project_path(PanelSettings.load().uefn_project_root)
    paths = known_project_roots(rescan=rescan)
    if current and current not in paths:
        paths.insert(0, current)
    rows: list[dict] = []
    for path in paths:
        kind = project_kind(path)
        slug = project_slug(path)
        rows.append(
            {
                "path": path,
                "name": project_display_name(path),
                "slug": slug,
                "kind": kind,
                "flavor": project_flavor(path),
                "mount": _mount_for(path, kind),
                "source": "uefn" if kind == UEFN else "other",
                "last_opened": opened.get(path, 0.0),
                "has_thumbnail": bool(kind == UEFN and thumbnail_path(path)),
                "active": path == current,
                # The details bar: whether the island already has Ducky's listener (boot
                # sets it up on every island). Duckies are one list, in no project.
                "listener_ready": kind == UEFN and (Path(path) / "Content" / "Python" / "init_unreal.py").is_file(),
            }
        )
    sources = [{"id": "uefn", "label": "UEFN"}, {"id": "other", "label": "Other"}]
    plugin_sources, plugin_rows = _plugin_rows()
    sources.extend(plugin_sources)
    taken = {r["path"] for r in rows}
    for row in plugin_rows:
        if row.get("path") in taken:
            # A plugin claims a folder Ducky already lists (a Roblox repo under Other).
            for r in rows:
                if r["path"] == row["path"]:
                    r["source"] = row["source"]
            continue
        rows.append(row)
    return {"sources": sources, "projects": rows}


def thumbnail_for_slug(slug: str) -> str:
    """Key-art file for a known project's slug ("" when none): the thumbnail route."""
    from frontend.ui_web.chats.store import project_slug

    for root in known_project_roots():
        if project_slug(root) == slug:
            return thumbnail_path(root)
    return ""


def islands_needing_setup() -> list[str]:
    return [p for p in known_project_roots() if has_uefnproject(p)]

