"""AppData housekeeping: sweep old backups, prune orphans, background startup sweep."""

from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path

from frontend.atomic_json import BACKUPS_DIR_NAME, prune_all_backups
from frontend.settings import PanelSettings, default_app_data_dir
from frontend.ui_web.project_chats import project_slug
from frontend.ui_web.recent_projects import load_recent_projects

_log = logging.getLogger(__name__)


def _known_project_slugs() -> set[str]:
    slugs: set[str] = set()
    for path in load_recent_projects():
        if path.strip():
            slugs.add(project_slug(path))
    current = PanelSettings.load().uefn_project_root.strip()
    if current:
        slugs.add(project_slug(current))
    return slugs


def _has_conversation_data(chats_project_dir: Path) -> bool:
    conv_dir = chats_project_dir / "conversations"
    if not conv_dir.is_dir():
        return False
    for path in conv_dir.glob("*.json"):
        if ".bak." in path.name:
            continue
        if path.is_file():
            return True
    for path in conv_dir.iterdir():
        if path.is_dir() and (path / "conversation.json").is_file():
            return True
    return False


def _should_keep_project_slug(slug: str, known_slugs: set[str], app_root: Path) -> bool:
    if slug in known_slugs:
        return True
    chats_dir = app_root / "chats" / "projects" / slug
    if chats_dir.is_dir() and _has_conversation_data(chats_dir):
        return True
    return False


def _safe_rmtree(path: Path) -> bool:
    try:
        shutil.rmtree(path)
        return True
    except OSError:
        return False


def sweep_old_backups(app_root: Path | None = None) -> int:
    """Move scattered *.bak.* files into app_root/backups/ (mirrored paths)."""
    if app_root is None:
        app_root = default_app_data_dir()
    backups_root = app_root / BACKUPS_DIR_NAME
    moved = 0
    for bak in app_root.rglob("*"):
        if not bak.is_file() or ".bak." not in bak.name:
            continue
        if backups_root in bak.parents or bak.parent == backups_root:
            continue
        try:
            rel = bak.relative_to(app_root)
        except ValueError:
            continue
        dest = backups_root / rel
        if dest.is_file():
            try:
                if bak.stat().st_mtime <= dest.stat().st_mtime:
                    bak.unlink()
                    moved += 1
                    continue
            except OSError:
                pass
            dest.unlink(missing_ok=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            bak.replace(dest)
            moved += 1
        except OSError:
            try:
                shutil.copy2(bak, dest)
                bak.unlink()
                moved += 1
            except OSError:
                pass
    return moved


def delete_project_appdata(slug: str, app_root: Path | None = None) -> int:
    """Remove all AppData for a project slug (chats, workspace, history, diagnostics, previews)."""
    if not (slug or "").strip():
        return 0
    if app_root is None:
        app_root = default_app_data_dir()
    removed = 0
    for area in (
        "chats/projects",
        "workspace/projects",
        "file_history",
        "verse_diagnostics",
    ):
        project_dir = app_root / Path(area) / slug
        if project_dir.is_dir() and _safe_rmtree(project_dir):
            removed += 1
    return removed


def prune_empty_project_dirs(app_root: Path | None = None) -> int:
    """Remove stub project folders not in recent list and without conversation data."""
    if app_root is None:
        app_root = default_app_data_dir()
    known = _known_project_slugs()
    removed = 0

    chats_root = app_root / "chats" / "projects"
    if chats_root.is_dir():
        for project_dir in list(chats_root.iterdir()):
            if not project_dir.is_dir():
                continue
            slug = project_dir.name
            if _should_keep_project_slug(slug, known, app_root):
                continue
            if _safe_rmtree(project_dir):
                removed += 1

    for area in (
        "workspace/projects",
        "file_history",
    ):
        root = app_root / Path(area)
        if not root.is_dir():
            continue
        for project_dir in list(root.iterdir()):
            if not project_dir.is_dir():
                continue
            slug = project_dir.name
            if _should_keep_project_slug(slug, known, app_root):
                continue
            if _safe_rmtree(project_dir):
                removed += 1

    return removed


def maintain_appdata(app_root: Path | None = None) -> dict[str, int]:
    """Run full AppData maintenance sweep."""
    if app_root is None:
        app_root = default_app_data_dir()
    moved = sweep_old_backups(app_root)
    pruned = prune_all_backups(app_root)
    removed_dirs = prune_empty_project_dirs(app_root)
    # Successful in-app upgrades leave Setup-*.exe under %TEMP%/UEFN-Ducky until
    # the relaunched panel starts — drop anything not newer than this build.
    try:
        from frontend.updater import sweep_installer_cache

        removed_installers = sweep_installer_cache()
    except Exception:
        removed_installers = 0
    result = {
        "moved_backups": moved,
        "pruned_backups": pruned,
        "removed_project_dirs": removed_dirs,
        "removed_installer_cache": removed_installers,
    }
    if any(result.values()):
        _log.info("AppData maintenance: %s", result)
    return result


def start_appdata_maintenance_async(app_root: Path | None = None) -> None:
    """Run maintenance in a background thread (panel / bridge startup)."""

    def _run() -> None:
        try:
            maintain_appdata(app_root)
        except Exception:
            _log.exception("AppData maintenance failed")

    threading.Thread(target=_run, daemon=True, name="appdata-maintenance").start()
