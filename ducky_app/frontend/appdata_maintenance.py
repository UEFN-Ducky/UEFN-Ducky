"""AppData housekeeping: sweep old backups, prune orphans, background startup sweep.

Also the Settings → App Data inventory (sizes, clear, delete).
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Any

from frontend.atomic_json import BACKUPS_DIR_NAME, prune_all_backups
from frontend.settings import PanelSettings, default_app_data_dir
from frontend.ui_web.project_chats import project_display_name, project_slug
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
    try:
        from backend.store.switch import use_db

        if use_db("chats"):
            from backend.store.repos import chats as repo

            if repo.conv_count(chats_project_dir.name) > 0:
                return True
    except Exception:
        pass
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


def _dir_has_files(path: Path) -> bool:
    if not path.is_dir():
        return False
    for _dirpath, _dirnames, filenames in os.walk(path):
        if filenames:
            return True
    return False


def _should_keep_project_slug(slug: str, known_slugs: set[str], app_root: Path) -> bool:
    del known_slugs  # known/recent is not enough to keep an empty stub
    chats_dir = app_root / "chats" / "projects" / slug
    if chats_dir.is_dir() and (_has_conversation_data(chats_dir) or _dir_has_files(chats_dir)):
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
    for bak in _walk_for_backups(app_root):
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


# Trees the app never writes .bak files into; walking them cost every boot
# (the WebView2 profile, screenshots, node_modules of the coding-agent SDK…).
_SWEEP_SKIP_DIRS = frozenset(
    {
        "webview2_browser", "tool_captures", "coding_agents", "verse-lsp", "legacy", "snapshots",
        "piper_tts", "meshy_hats", "meshy_free", "asset_previews", "mesh_previews", "listener",
        "uefn_plugins", "skill_packs", "ai_plugins", "mcp_plugins", "setup-engine", "exports", "imports",
    }
)


def _walk_for_backups(app_root: Path):
    for dirpath, dirnames, filenames in os.walk(app_root):
        rel_top = Path(dirpath).relative_to(app_root).parts
        if rel_top and rel_top[0] in _SWEEP_SKIP_DIRS:
            dirnames[:] = []
            continue
        for name in filenames:
            if ".bak." in name:
                yield Path(dirpath) / name


def _retire_legacy_after_clean_boots(app_root: Path, *, needed: int = 3) -> int:
    """ADR 0003 §7: legacy/ (the pre-database files) is deleted once the store has
    passed its integrity check on three separate boots."""
    try:
        from backend.store.repos import kv
        from backend.store.switch import use_db

        if not use_db("settings"):
            return 0
        legacy = app_root / "legacy"
        if not legacy.is_dir():
            return 0
        boots = int(kv.meta_get("clean_boots") or 0) + 1
        kv.meta_set("clean_boots", str(boots))
        if boots < needed:
            return 0
        shutil.rmtree(legacy, ignore_errors=True)
        _log.info("AppData maintenance: removed legacy/ after %d clean boots", boots)
        return 1
    except Exception:
        _log.exception("legacy retirement failed")
        return 0


# (rel_parent, size-label) — folders that hold one dir per project slug.
_PROJECT_AREAS: tuple[tuple[str, str], ...] = (
    ("chats/projects", "Chats"),
    ("workspace/projects", "Workspace"),
    ("file_history", "File history"),
    ("verse_diagnostics", "Diagnostics"),
    ("changesets", "Changesets"),
    ("memory/projects", "Memory"),
)

_CHILD_LIMIT = 80

# name -> (label, description, kind)
# kind: cache | user | install | runtime | settings | other
_KNOWN: dict[str, tuple[str, str, str]] = {
    "chats": ("Chats leftover", "Pre-database conversation files. Safe to delete after import.", "cache"),
    "workspace": ("Workspace leftover", "Pre-database editor layout. Now in ducky.db.", "cache"),
    "file_history": ("File history leftover", "Pre-database undo snapshots. Now in ducky.db.", "cache"),
    "verse_diagnostics": ("Diagnostics leftover", "Pre-database Verse scan cache. Now in ducky.db.", "cache"),
    "changesets": ("Changesets leftover", "Pre-database write ledger. Now in ducky.db.", "cache"),
    "memory": ("Memory leftover", "Pre-database notes. Now in ducky.db.", "cache"),
    "tool_captures": ("Captures", "Screenshots and snips from tools.", "cache"),
    "backups": ("Backups leftover", "Old JSON .bak copies. Snapshots replaced this.", "cache"),
    "listener": ("Listener", "UEFN Python listener shipped here. Recreated on app start.", "runtime"),
    "skill_packs": ("Skill packs", "Installed skill packs.", "install"),
    "uefn_plugins": ("Desktop plugins", "Installed Store plugins (each plugin stays in its own folder).", "install"),
    "mcp_plugins": ("MCP plugins leftover", "Legacy MCP packs. Unused after Store plugins.", "cache"),
    "ai_plugins": ("AI plugin drafts", "In-progress plugin drafts.", "install"),
    "coding_agents": ("Coding agents", "Shared CLI agent temp files and bridge copies.", "cache"),
    "verse-lsp": ("Verse LSP", "Language server cache.", "cache"),
    "plan_templates": ("Plan templates leftover", "Pre-database templates. Now in ducky.db.", "cache"),
    "verse_templates": ("Verse templates", "Custom Verse templates.", "user"),
    "perf": ("Perf leftover", "Pre-database traces. Now events in ducky.db.", "cache"),
    "tasks": ("Tasks leftover", "Pre-database task files. Now in ducky.db.", "cache"),
    "sounds": ("Sounds", "Custom notification audio.", "user"),
    "duckies": ("Custom duckies", "Custom ducky avatars.", "user"),
    "uefn_plugin_cache": ("Plugin cache leftover", "Now plugin_kv rows in ducky.db.", "cache"),
    "uefn_plugin_prefs": ("Plugin prefs leftover", "Now plugin_kv rows in ducky.db.", "cache"),
    "webview2_browser": ("In-app browser", "Embedded browser profile (Discord login, cookies).", "cache"),
    "panel_settings.json": ("Settings leftover", "Pre-database settings. Now rows in ducky.db.", "cache"),
    "config.json": ("Config", "Listener config JSON (projection).", "settings"),
    "credentials.dat": ("Credentials leftover", "Pre-database keys. Now secrets rows.", "cache"),
    "recent_projects.json": ("Recent leftover", "Pre-database project list. Now in ducky.db.", "cache"),
    "workspace_dock.json": ("Dock leftover", "Pre-database dock layout. Now in ducky.db.", "cache"),
    "models_cache.json": ("Models leftover", "Pre-database catalog. Now cache_docs in ducky.db.", "cache"),
    "agent_crashes.jsonl": ("Crashes leftover", "Pre-database crash log. Now events in ducky.db.", "cache"),
    "uefn_plugin_load_errors.jsonl": ("Plugin errors leftover", "Now events in ducky.db.", "cache"),
    "mcp.json": ("MCP config", "MCP server list export. Rows in ducky.db are source of truth.", "settings"),
    "pyinstaller-work": ("Build scratch", "PyInstaller work dir. Does not belong in AppData.", "cache"),
    "ship_stamp.json": ("Ship stamp", "Last listener/skill deploy stamp.", "runtime"),
    "setup-progress.txt": ("Setup progress", "Installer host progress file.", "runtime"),
    "panel.pid": ("Panel lock", "Running panel process id.", "runtime"),
    # ADR 0003: the store and its sidecars. Never clearable, never deletable.
    "ducky.db": ("Database", "All app state (chats, settings, ledger, plans, memory).", "settings"),
    "ducky.db-wal": ("Database log", "SQLite write-ahead log for ducky.db.", "settings"),
    "ducky.db-shm": ("Database index", "SQLite shared-memory index for ducky.db.", "settings"),
    "snapshots": ("Database snapshots", "Consistent copies of ducky.db (newest 3).", "settings"),
    "legacy": ("Legacy stores", "Pre-database files kept until three clean boots.", "cache"),
    "exports": ("Exports", "Tables exported from Settings → App Data → Database.", "user"),
}

_PROTECTED_NAMES = frozenset(
    {
        "credentials.dat",
        "panel_settings.json",
        "config.json",
        "mcp.json",
        "listener",
        "ducky.db",
        "ducky.db-wal",
        "ducky.db-shm",
        "snapshots",
    }
)


def delete_project_appdata(slug: str, app_root: Path | None = None) -> int:
    """Remove all AppData for a project slug (chats, workspace, history, diagnostics, …)."""
    if not (slug or "").strip():
        return 0
    if app_root is None:
        app_root = default_app_data_dir()
    removed = 0
    for area, _label in _PROJECT_AREAS:
        project_dir = app_root / Path(area) / slug
        if project_dir.is_dir() and _safe_rmtree(project_dir):
            removed += 1
    # ADR 0003: the project's rows (chats, ledger, history, plans, memory, diagnostics).
    try:
        from backend.store.switch import use_db

        if use_db("settings"):
            from frontend.store_admin import delete_project_rows

            removed += sum(delete_project_rows(slug).values())
    except Exception:
        _log.exception("deleting project rows for %s failed", slug)
    return removed


def prune_empty_project_dirs(app_root: Path | None = None) -> int:
    """Remove stub project folders that hold no conversation rows and no files."""
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
        "changesets",
        "verse_diagnostics",
        "memory/projects",
    ):
        root = app_root / Path(area)
        if not root.is_dir():
            continue
        for project_dir in list(root.iterdir()):
            if not project_dir.is_dir():
                continue
            if _dir_has_files(project_dir):
                continue
            if _safe_rmtree(project_dir):
                removed += 1

    return removed


# Empty leftover trees from the pre-database layout. Never recreate these.
_EMPTY_LEFTOVER_ROOTS = (
    "chats",
    "workspace",
    "file_history",
    "changesets",
    "memory",
    "plan_templates",
    "uefn_plugin_cache",
    "uefn_plugin_prefs",
    "perf",
    "verse_diagnostics",
    "backups",
    "mcp_plugins",
    "tasks",
)


def prune_empty_leftover_dirs(app_root: Path | None = None) -> int:
    """Delete empty leftover folders and build scratch that does not belong in AppData."""
    if app_root is None:
        app_root = default_app_data_dir()
    removed = 0
    work = app_root / "pyinstaller-work"
    if work.is_dir() and _safe_rmtree(work):
        removed += 1
    tmp = app_root / "coding_agents" / "tmp"
    if tmp.is_dir():
        try:
            empty = not any(tmp.iterdir())
        except OSError:
            empty = False
        if empty:
            try:
                tmp.rmdir()
                removed += 1
            except OSError:
                pass
    for name in _EMPTY_LEFTOVER_ROOTS:
        top = app_root / name
        if not top.is_dir():
            continue
        for dirpath, _dirnames, _filenames in os.walk(top, topdown=False):
            path = Path(dirpath)
            try:
                if not any(path.iterdir()):
                    path.rmdir()
                    removed += 1
            except OSError:
                pass
    return removed


def heal_plugin_runtime_homes(app_root: Path | None = None) -> int:
    """Move plugin runtimes that leaked to AppData root into uefn_plugins/<id>/."""
    if app_root is None:
        app_root = default_app_data_dir()
    src = app_root / "unity_mcp"
    if not src.is_dir():
        return 0
    dest = app_root / "uefn_plugins" / "unity-mcp" / "runtime"
    plugin_dir = dest.parent
    if dest.exists():
        shutil.rmtree(src, ignore_errors=True)
        return 1
    if not plugin_dir.is_dir():
        shutil.rmtree(src, ignore_errors=True)
        return 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        src.rename(dest)
        return 1
    except OSError:
        try:
            shutil.copytree(src, dest, dirs_exist_ok=True)
            shutil.rmtree(src, ignore_errors=True)
            return 1
        except OSError:
            return 0


def maintain_appdata(app_root: Path | None = None, *, count_boot: bool = True) -> dict[str, int]:
    """Run full AppData maintenance sweep.

    ``count_boot`` is True for the panel process only: the bridge runs the same
    sweep, and a boot must count once toward legacy/ retirement, not twice."""
    if app_root is None:
        app_root = default_app_data_dir()
    db_result = _maintain_store(app_root, count_boot=count_boot)
    db_mode = False
    try:
        from backend.store.switch import use_db

        db_mode = use_db("settings")
    except Exception:
        pass
    if db_mode:
        moved = 0
        pruned = 0
    else:
        moved = sweep_old_backups(app_root)
        pruned = prune_all_backups(app_root)
    removed_dirs = prune_empty_project_dirs(app_root)
    removed_dirs += prune_empty_leftover_dirs(app_root)
    removed_dirs += heal_plugin_runtime_homes(app_root)
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
        **db_result,
    }
    if any(result.values()):
        _log.info("AppData maintenance: %s", result)
    return result


_SNAPSHOT_EVERY_S = 24 * 3600


def _maintain_store(app_root: Path, *, count_boot: bool = True) -> dict[str, int]:
    """ADR 0003: integrity check (restores the newest snapshot on failure) and a
    daily ``VACUUM INTO`` snapshot. Never raises: maintenance must not take the
    panel down, and the store logs its own failures."""
    import time

    out = {"db_checked": 0, "db_snapshot": 0}
    try:
        from backend.store import db as store_db
        from backend.store.switch import use_db

        if not use_db("settings"):
            return out
        conn = store_db.open_checked(app_root)
        out["db_checked"] = 1
        store_db.record_integrity(conn, "ok")
        # Upgrade path: every legacy store is imported on the first boot, not
        # lazily on first use, so legacy/ is complete before its countdown starts.
        from backend.store.importers.boot import ensure_all_stores

        out["db_imported"] = sum(1 for r in ensure_all_stores().values() if r is not None)
        out["legacy_removed"] = _retire_legacy_after_clean_boots(app_root) if count_boot else 0
        newest = store_db.newest_snapshot(app_root)
        if newest is None or time.time() - newest.stat().st_mtime > _SNAPSHOT_EVERY_S:
            store_db.snapshot(app_root, label="daily")
            out["db_snapshot"] = 1
    except Exception:
        _log.exception("ducky.db maintenance failed")
    return out


def start_appdata_maintenance_async(app_root: Path | None = None, *, count_boot: bool = True) -> None:
    """Run maintenance in a background thread (panel / bridge startup)."""

    def _run() -> None:
        try:
            maintain_appdata(app_root, count_boot=count_boot)
        except Exception:
            _log.exception("AppData maintenance failed")

    threading.Thread(target=_run, daemon=True, name="appdata-maintenance").start()


def resolve_appdata_rel(rel: str, app_root: Path | None = None) -> Path | None:
    """Resolve ``rel`` under AppData. None if it escapes the root."""
    root = (app_root or default_app_data_dir()).resolve()
    raw = (rel or "").replace("\\", "/").strip("/")
    if not raw:
        return root
    if any(part in ("..", "") for part in raw.split("/")):
        return None
    path = (root / raw).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def _walk_stats(path: Path) -> tuple[int, int, int]:
    """Return (bytes, files, dirs) without following symlinks."""
    size = 0
    files = 0
    dirs = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            dirs += 1
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            files += 1
                            size += int(entry.stat(follow_symlinks=False).st_size)
                    except OSError:
                        continue
        except OSError:
            continue
    return size, files, dirs


def _rel_to_root(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _describe(name: str) -> tuple[str, str, str]:
    hit = _KNOWN.get(name)
    if hit:
        return hit
    return (name, "Unlisted AppData item.", "other")


def _flags(name: str, kind: str) -> tuple[bool, bool, bool]:
    """clearable, deletable, protected — top-level name only (not nested children)."""
    if name in _PROTECTED_NAMES or kind in ("runtime", "settings"):
        return False, False, True
    return True, True, False


def _item_dict(path: Path, root: Path, *, nested: bool = False) -> dict[str, Any]:
    name = path.name
    rel = _rel_to_root(path, root)
    label, description, kind = _describe(name if not nested else name)
    if nested:
        # Nested rows keep the folder name; kind follows the top-level ancestor
        # when known, else other. Don't apply top-level protected flags to a
        # screenshot inside tool_captures.
        top = rel.split("/", 1)[0] if rel else name
        _label, description, kind = _describe(top)
        label = name
        clearable, deletable, protected = True, True, False
        if top in _PROTECTED_NAMES:
            clearable, deletable, protected = False, False, True
    else:
        clearable, deletable, protected = _flags(name, kind)
    is_dir = path.is_dir() and not path.is_symlink()
    if is_dir:
        size, files, dirs = _walk_stats(path)
    elif path.is_file():
        try:
            size = int(path.stat().st_size)
        except OSError:
            size = 0
        files, dirs = 1, 0
    else:
        size, files, dirs = 0, 0, 0
    return {
        "name": name,
        "rel": rel,
        "is_dir": is_dir,
        "bytes": size,
        "files": files,
        "dirs": dirs,
        "kind": kind,
        "label": label,
        "description": description,
        "clearable": clearable,
        "deletable": deletable,
        "protected": protected,
    }


def appdata_overview(app_root: Path | None = None) -> dict[str, Any]:
    root = (app_root or default_app_data_dir()).resolve()
    root.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    try:
        with os.scandir(root) as it:
            names = sorted((e.name for e in it), key=str.lower)
    except OSError:
        names = []
    for name in names:
        path = root / name
        items.append(_item_dict(path, root, nested=False))
    items.sort(key=lambda row: (-int(row["bytes"]), str(row["name"]).lower()))
    return {
        "root": str(root),
        "bytes": sum(int(row["bytes"]) for row in items),
        "files": sum(int(row["files"]) for row in items),
        "dirs": sum(int(row["dirs"]) for row in items),
        "items": items,
    }


def appdata_children(rel: str, app_root: Path | None = None) -> dict[str, Any]:
    root = (app_root or default_app_data_dir()).resolve()
    path = resolve_appdata_rel(rel, root)
    if path is None or not path.is_dir():
        return {"rel": rel or "", "items": [], "truncated": False, "total": 0, "error": "not_found"}
    rows: list[dict[str, Any]] = []
    try:
        with os.scandir(path) as it:
            children = sorted((Path(e.path) for e in it), key=lambda p: p.name.lower())
    except OSError:
        children = []
    for child in children:
        rows.append(_item_dict(child, root, nested=True))
    rows.sort(key=lambda row: (-int(row["bytes"]), str(row["name"]).lower()))
    total = len(rows)
    truncated = total > _CHILD_LIMIT
    return {
        "rel": _rel_to_root(path, root) if path != root else "",
        "items": rows[:_CHILD_LIMIT],
        "truncated": truncated,
        "total": total,
    }


def _clear_contents(path: Path) -> int:
    if path.is_file() or path.is_symlink():
        try:
            path.unlink()
            return 1
        except OSError:
            return 0
    if not path.is_dir():
        return 0
    removed = 0
    try:
        children = list(path.iterdir())
    except OSError:
        return 0
    for child in children:
        if child.is_symlink() or child.is_file():
            try:
                child.unlink()
                removed += 1
            except OSError:
                pass
        elif child.is_dir():
            if _safe_rmtree(child):
                removed += 1
    return removed


def appdata_clear(rel: str, app_root: Path | None = None) -> dict[str, Any]:
    """Empty a folder (keep the folder) or delete a file. Refuses protected names."""
    root = (app_root or default_app_data_dir()).resolve()
    raw = (rel or "").replace("\\", "/").strip("/")
    if not raw:
        return {"ok": False, "error": "refused_root", "removed": 0}
    path = resolve_appdata_rel(raw, root)
    if path is None:
        return {"ok": False, "error": "not_found", "removed": 0}
    top = raw.split("/", 1)[0]
    if top in _PROTECTED_NAMES:
        return {"ok": False, "error": "protected", "removed": 0}
    if not path.exists():
        return {"ok": False, "error": "not_found", "removed": 0}
    removed = _clear_contents(path)
    return {"ok": True, "removed": removed}


def appdata_delete(rel: str, app_root: Path | None = None) -> dict[str, Any]:
    """Delete a file or folder under AppData. Refuses protected names and the root."""
    root = (app_root or default_app_data_dir()).resolve()
    raw = (rel or "").replace("\\", "/").strip("/")
    if not raw:
        return {"ok": False, "error": "refused_root", "removed": 0}
    path = resolve_appdata_rel(raw, root)
    if path is None:
        return {"ok": False, "error": "not_found", "removed": 0}
    top = raw.split("/", 1)[0]
    if top in _PROTECTED_NAMES:
        return {"ok": False, "error": "protected", "removed": 0}
    if not path.exists():
        return {"ok": False, "error": "not_found", "removed": 0}
    if path.is_dir() and not path.is_symlink():
        ok = _safe_rmtree(path)
        return {"ok": ok, "removed": 1 if ok else 0, "error": None if ok else "busy"}
    try:
        path.unlink()
        return {"ok": True, "removed": 1}
    except OSError:
        return {"ok": False, "error": "busy", "removed": 0}


def appdata_clear_caches(app_root: Path | None = None) -> dict[str, Any]:
    """Clear every top-level item whose kind is cache (not chats, plugins, settings)."""
    overview = appdata_overview(app_root)
    removed = 0
    cleared: list[str] = []
    for row in overview["items"]:
        if row["kind"] != "cache" or not row["clearable"]:
            continue
        result = appdata_clear(str(row["rel"]), app_root)
        if result.get("ok"):
            removed += int(result.get("removed") or 0)
            cleared.append(str(row["rel"]))
    return {"ok": True, "removed": removed, "cleared": cleared}


def appdata_projects(app_root: Path | None = None) -> dict[str, Any]:
    root = (app_root or default_app_data_dir()).resolve()
    slug_to_path: dict[str, str] = {}
    for project_path in load_recent_projects():
        if project_path.strip():
            slug_to_path[project_slug(project_path)] = project_path
    current = PanelSettings.load().uefn_project_root.strip()
    if current:
        slug_to_path.setdefault(project_slug(current), current)

    slugs: set[str] = set(slug_to_path)
    row_counts: dict[str, dict[str, int]] = {}
    try:
        from backend.store.switch import use_db

        if use_db("settings"):
            from frontend.store_admin import project_row_counts

            row_counts = project_row_counts()
            slugs.update(row_counts)
    except Exception:
        _log.exception("project row counts failed")
    for area, _label in _PROJECT_AREAS:
        area_root = root / Path(area)
        if not area_root.is_dir():
            continue
        try:
            for child in area_root.iterdir():
                if child.is_dir():
                    slugs.add(child.name)
        except OSError:
            continue

    projects: list[dict[str, Any]] = []
    for slug in slugs:
        areas: list[dict[str, Any]] = []
        total = 0
        for area, label in _PROJECT_AREAS:
            project_dir = root / Path(area) / slug
            if not project_dir.is_dir():
                continue
            size, files, dirs = _walk_stats(project_dir)
            areas.append(
                {
                    "name": label,
                    "rel": _rel_to_root(project_dir, root),
                    "bytes": size,
                    "files": files,
                    "dirs": dirs,
                }
            )
            total += size
        rows = row_counts.get(slug, {})
        if not areas and not rows:
            continue
        project_path = slug_to_path.get(slug, "")
        label = project_display_name(project_path) if project_path else slug
        projects.append(
            {
                "slug": slug,
                "label": label,
                "path": project_path,
                "bytes": total,
                "areas": areas,
                "rows": rows,
            }
        )
    projects.sort(key=lambda row: (-int(row["bytes"]), str(row["label"]).lower()))
    return {"projects": projects, "bytes": sum(int(p["bytes"]) for p in projects)}
