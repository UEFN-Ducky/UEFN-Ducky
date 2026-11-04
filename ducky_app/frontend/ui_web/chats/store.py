"""Where chats live: app-data paths, project slugs, the row-store switch and per-chat locks."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from frontend.settings import PanelSettings, default_app_data_dir

_migrate_lock_guard = threading.Lock()
_migrate_locks: dict[str, threading.Lock] = {}
_conv_lock_guard = threading.Lock()
_conv_locks: dict[str, threading.RLock] = {}


def release_conversation_lock(conv_id: str) -> None:
    """Drop the per-conv lock after delete so the dict does not grow forever."""
    with _conv_lock_guard:
        _conv_locks.pop(conv_id, None)


def _conversation_lock(conv_id: str) -> threading.RLock:
    with _conv_lock_guard:
        lock = _conv_locks.get(conv_id)
        if lock is None:
            lock = threading.RLock()
            _conv_locks[conv_id] = lock
        return lock


def _conversation_migrate_lock(project_root: str | None) -> threading.Lock:
    slug = _project_id(project_root)
    with _migrate_lock_guard:
        lock = _migrate_locks.get(slug)
        if lock is None:
            lock = threading.Lock()
            _migrate_locks[slug] = lock
        return lock


def project_slug(project_root: str) -> str:
    raw = (project_root or "").strip()
    if not raw:
        return "_no_project"
    try:
        resolved = str(Path(raw).resolve())
    except OSError:
        resolved = raw
    digest = hashlib.sha256(resolved.lower().encode("utf-8")).hexdigest()[:16]
    name = Path(raw).name or "project"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:40]
    return f"{safe}_{digest}"


def project_display_name(project_root: str) -> str:
    raw = (project_root or "").strip()
    if not raw:
        return "No project"
    return Path(raw).name or raw


def _chats_root() -> Path:
    return default_app_data_dir() / "chats" / "projects"


def _project_root(project_root: str | None = None, *, create: bool = False) -> Path:
    d = _chats_root() / _project_id(project_root)
    if create or not _use_db():
        d.mkdir(parents=True, exist_ok=True)
    return d


def _folders_path(project_root: str | None = None) -> Path:
    return _project_root(project_root) / "folders.json"


def _conversations_dir(project_root: str | None = None, *, create: bool = False) -> Path:
    d = _project_root(project_root, create=create) / "conversations"
    if create or not _use_db():
        d.mkdir(parents=True, exist_ok=True)
    return d


def get_conversations_dir(project_root: str | None = None, *, create: bool = False) -> Path:
    return _conversations_dir(project_root, create=create)


def _use_db() -> bool:
    from backend.store.switch import use_db

    return use_db("chats")


# Every ducky, group and folder lives in one list (Oct 23 2026, the owner: duckies work on
# any project, and the selected project only drives the Content tab). A project a caller
# names is no longer where a chat is stored; merge_into_one_list moves older rows here.
ONE_LIST_SLUG = "_no_project"


def _project_id(project_root: str | None = None) -> str:
    del project_root
    return ONE_LIST_SLUG


def _repo():
    """The chats repo, with the one-time legacy import done."""
    from backend.store.importers import phase2
    from backend.store.repos import chats as repo

    phase2.ensure()
    return repo


def _slug_display_names() -> dict[str, str]:
    from frontend.ui_web.known_projects import known_project_roots

    names: dict[str, str] = {}
    for path in known_project_roots():
        names[project_slug(path)] = project_display_name(path)
    current = (PanelSettings.load().uefn_project_root or "").strip()
    if current:
        names.setdefault(project_slug(current), project_display_name(current))
    return names


def _iter_disk_project_slugs() -> list[str]:
    root = _chats_root()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def project_slug_display_name(slug: str) -> str:
    if slug == "_no_project":
        return "No project"
    return _slug_display_names().get(slug) or slug


def create_project_root(target_slug: str, current_root: str) -> str | None:
    """Where a new ducky is stored. None keeps the open island. '' is no island."""
    target = (target_slug or "").strip()
    if not target or project_slug(current_root) == target:
        return None
    if target == "_no_project":
        return ""
    root = project_root_for_slug(target)
    if not root:
        raise ValueError(f"Unknown project: {target}")
    return root


def project_root_for_slug(slug: str) -> str | None:
    """Project path for a sidebar project row: the open project or any known one."""
    wanted = (slug or "").strip()
    if not wanted:
        return None
    current = (PanelSettings.load().uefn_project_root or "").strip()
    if current and project_slug(current) == wanted:
        return current
    from frontend.ui_web.known_projects import known_project_roots

    for path in known_project_roots():
        if project_slug(path) == wanted:
            return path
    return None


def _conversation_disk_dir(slug: str, conv_id: str) -> Path:
    return _chats_root() / slug / "conversations" / conv_id
