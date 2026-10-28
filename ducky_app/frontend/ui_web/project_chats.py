"""Project-scoped chat folders and conversations (wraps paths; does not modify chat_store)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import Future
from copy import copy as _shallow
from pathlib import Path
from typing import Any

from frontend.archive_folder import (
    ARCHIVE_FOLDER_ID,
    assert_not_archive_folder,
    ensure_archive_folder,
    is_archive_folder_id,
)
from frontend.atomic_json import write_json_atomic
from frontend.chat_store import (
    ChatFolder,
    Conversation,
    norm_file_path,
    normalize_ducky_style,
    remap_conversation_file_path,
)
from frontend.settings import PanelSettings, default_app_data_dir

_migrate_lock_guard = threading.Lock()
_migrate_locks: dict[str, threading.Lock] = {}
_conv_lock_guard = threading.Lock()
_conv_locks: dict[str, threading.RLock] = {}
_sort_migrated_slugs: set[str] = set()
_writer_guard = threading.Lock()
_host_refused: set[str] = set()  # chats whose refused message save was logged once
_WRITER: Any = None  # the chat-writer (row store only), made on the first save


def _writer():
    global _WRITER
    with _writer_guard:
        if _WRITER is None:
            from backend.store import db
            from frontend.ui_web.chat_writer import ChatWriter

            _WRITER = ChatWriter(_write_queued, connect=db.connect, root=db.app_root)
        return _WRITER


def flush_chat_saves(conv_id: str | None = None, timeout: float = 30.0) -> bool:
    """Write queued chat saves now (one chat's or all); True once written. Cheap when
    nothing waits. Called before a direct row write and before /__panel_rpc answers."""
    writer = _WRITER
    return True if writer is None else writer.flush(conv_id, timeout)


def _forget_saved(conv_id: str | None = None) -> None:
    if _WRITER is not None:
        _WRITER.forget(conv_id)


def _queued(conv_id: str) -> Conversation | None:
    """This chat's copy still waiting for the writer, if any."""
    writer = _WRITER
    item = writer.peek(conv_id) if writer is not None else None
    return item[0] if item else None


def _write_queued(conv_id: str, item: tuple[Conversation, str | None]) -> dict[str, Any]:
    del conv_id
    conv, project_root = item
    return _save_conversation_rows(conv, project_root)


def _host_owned(conv_id: str) -> bool:
    """An agent host runs this chat's turn now: the host writes its messages, the app
    only its metadata, so the two never race on the same rows."""
    try:
        from frontend.agent_host.turns import owns
    except Exception:
        return False
    return owns(conv_id) is not None


def _refuse_host_messages(conv_id: str) -> None:
    if conv_id not in _host_refused:
        _host_refused.add(conv_id)
        logging.getLogger(__name__).warning(
            "chat %s runs in an agent host: saved its metadata, not its messages", conv_id
        )


def _is_assistant(message: Any) -> bool:
    return isinstance(message, dict) and message.get("role") == "assistant"


def _keeps_stored_rows(incoming: list[Any], stored: list[Any]) -> bool:
    """The stub guard: an empty copy, or one that lost the stored assistant rows, never
    replaces a transcript."""
    if not incoming:
        return True
    return not any(_is_assistant(m) for m in incoming) and any(_is_assistant(m) for m in stored)


def _from_queue(conv: Conversation, *, with_messages: bool) -> Conversation:
    """A reader's own copy of a queued save, with the transcript the writer will keep."""
    out = Conversation.from_dict({**conv.to_dict(), "messages": []})
    if not with_messages:
        return out
    mine = list(conv.messages or [])
    if not mine or not any(_is_assistant(m) for m in mine):
        stored = _repo().messages_get(conv.id)  # a stub save never blanks the stored rows
        if stored and (not mine or any(_is_assistant(m) for m in stored)):
            mine = stored
    out.messages = mine
    return out


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


def _stats_for(messages: list[Any]) -> dict[str, int]:
    from frontend.ui_web.session_files import session_stats_from_messages

    return session_stats_from_messages(messages)


# conv id -> {row hash: (tool calls, file paths)}. A save scanned every message of the
# chat for these counts (55% of a 5,000-message save); now only rows it has not seen.
# Keyed on the row hash, so rows another writer (an agent host) changed are rescanned.
_ROW_STATS: OrderedDict[str, dict[str, tuple[int, frozenset[str]]]] = OrderedDict()
_ROW_STATS_CHATS = 32
_row_stats_lock = threading.Lock()


def _stats_by_hash(conv_id: str, messages: list[Any], hashes: list[str]) -> dict[str, int]:
    from frontend.ui_web.session_files import message_stats

    if len(hashes) != len(messages):
        return _stats_for(messages)
    with _row_stats_lock:
        known = _ROW_STATS.pop(conv_id, {})
    rows: dict[str, tuple[int, frozenset[str]]] = {}
    count = 0
    paths: set[str] = set()
    for message, h in zip(messages, hashes):
        stat = rows.get(h) or known.get(h)
        if stat is None:
            stat = message_stats(message)
        rows[h] = stat
        count += stat[0]
        paths |= stat[1]
    with _row_stats_lock:
        _ROW_STATS[conv_id] = rows
        while len(_ROW_STATS) > _ROW_STATS_CHATS:
            _ROW_STATS.popitem(last=False)
    return {"tool_call_count": count, "file_count": len(paths)}


def _default_folders() -> list[dict[str, Any]]:
    return []


def _folder_sort_key(folder: ChatFolder) -> tuple[str, float, str]:
    return (folder.parent_id or "", folder.sort_order, folder.id)


def _sort_folders(folders: list[ChatFolder]) -> list[ChatFolder]:
    return sorted(folders, key=_folder_sort_key)


def _conversation_sort_key(conv: Conversation) -> tuple[float, float, str]:
    return (conv.sort_order, -conv.updated, conv.id)


def _sort_conversations(convs: list[Conversation]) -> list[Conversation]:
    return sorted(convs, key=_conversation_sort_key)


def _migrate_folder_sort_orders(folders: list[ChatFolder]) -> bool:
    """Assign sort_order from sibling index when every sibling in a group is still 0."""
    changed = False
    by_parent: dict[str, list[ChatFolder]] = {}
    for folder in folders:
        by_parent.setdefault(folder.parent_id or "", []).append(folder)
    for group in by_parent.values():
        if len(group) <= 1:
            continue
        if all(f.sort_order == 0.0 for f in group):
            for idx, folder in enumerate(group):
                folder.sort_order = float(idx)
                changed = True
    return changed


def _migrate_conversation_sort_orders(convs: list[Conversation]) -> list[Conversation]:
    """Assign sort_order from updated desc within each folder when all are zero."""
    changed: list[Conversation] = []
    by_folder: dict[str, list[Conversation]] = {}
    for conv in convs:
        by_folder.setdefault(conv.folder_id or "", []).append(conv)

    for group in by_folder.values():
        if not group:
            continue
        if all(c.sort_order == 0.0 for c in group):
            ordered = sorted(group, key=lambda c: c.updated, reverse=True)
            for idx, conv in enumerate(ordered):
                conv.sort_order = float(idx)
                changed.append(conv)
    return changed


def _load_all_conversations(
    project_root: str | None = None,
    *,
    include_messages: bool = True,
) -> list[Conversation]:
    from frontend.ui_web.session_files import session_stats_from_messages

    if _use_db():
        flush_chat_saves()  # a list reads the store: queued saves first (cheap when none)
        # Counts are maintained columns; a metadata list never touches message bodies.
        docs = _repo().conv_list(_project_id(project_root), with_messages=include_messages)
        return [Conversation.from_dict(doc) for doc in docs]
    out: list[Conversation] = []
    seen: set[str] = set()
    conv_dir = _conversations_dir(project_root)
    for path in conv_dir.iterdir():
        if not path.is_dir():
            continue
        meta = path / "conversation.json"
        if not meta.is_file():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            msgs = data.get("messages") if isinstance(data.get("messages"), list) else []
            stats = session_stats_from_messages(msgs)
            data = {
                **data,
                "tool_call_count": stats["tool_call_count"],
                "file_count": stats["file_count"],
            }
            if not include_messages:
                data = {**data, "messages": []}
            conv = Conversation.from_dict(data)
            if conv.id in seen:
                continue
            seen.add(conv.id)
            out.append(conv)
        except (json.JSONDecodeError, OSError):
            continue
    return out


def _maybe_migrate_conversation_sort_orders(project_root: str | None = None) -> None:
    """One-shot per process/project: fix zeroed sort_order without running on every list."""
    slug = _project_id(project_root)
    if slug in _sort_migrated_slugs:
        return
    with _conversation_migrate_lock(project_root):
        if slug in _sort_migrated_slugs:
            return
        all_convs = _load_all_conversations(project_root, include_messages=not _use_db())
        for conv in _migrate_conversation_sort_orders(all_convs):
            save_conversation(conv, project_root, touch_updated=False)
        _sort_migrated_slugs.add(slug)


def _folder_by_id(folders: list[ChatFolder]) -> dict[str, ChatFolder]:
    return {f.id: f for f in folders}


def _would_create_cycle(folders: list[ChatFolder], folder_id: str, new_parent_id: str) -> bool:
    if not new_parent_id or folder_id == new_parent_id:
        return folder_id == new_parent_id and folder_id != ""
    by_id = _folder_by_id(folders)
    current = new_parent_id
    while current:
        if current == folder_id:
            return True
        parent = by_id.get(current)
        if parent is None:
            break
        current = parent.parent_id or ""
    return False


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


def _load_folders_for_slug(slug: str) -> list[ChatFolder]:
    if _use_db():
        return [ChatFolder.from_dict(f) for f in _repo().folders_get(slug)]
    path = _chats_root() / slug / "folders.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("folders") if isinstance(data, dict) else data
        if not isinstance(raw, list):
            return []
        return _sort_folders([ChatFolder.from_dict(f) for f in raw if isinstance(f, dict)])
    except (json.JSONDecodeError, OSError):
        return []


def _load_conversations_for_slug(slug: str) -> list[Conversation]:
    if _use_db():
        return [Conversation.from_dict(d) for d in _repo().conv_list(slug, with_messages=False)]
    out: list[Conversation] = []
    conv_dir = _chats_root() / slug / "conversations"
    if not conv_dir.is_dir():
        return out
    for path in conv_dir.iterdir():
        if not path.is_dir():
            continue
        meta = path / "conversation.json"
        if not meta.is_file():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            data = {**data, "messages": []}
            out.append(Conversation.from_dict(data))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def iter_folders_by_project() -> list[tuple[str, list[ChatFolder]]]:
    if _use_db():
        return [
            (pid, [ChatFolder.from_dict(f) for f in rows])
            for pid, rows in _repo().folders_list_all_projects()
        ]
    return [(slug, _load_folders_for_slug(slug)) for slug in _iter_disk_project_slugs()]


def iter_conversations_by_project() -> list[tuple[str, Conversation]]:
    if _use_db():
        return [
            (pid, Conversation.from_dict(data))
            for pid, data in _repo().conv_list_all_projects(with_messages=False)
        ]
    out: list[tuple[str, Conversation]] = []
    for slug in _iter_disk_project_slugs():
        for conv in _load_conversations_for_slug(slug):
            out.append((slug, conv))
    return out


def project_slug_display_name(slug: str) -> str:
    if slug == "_no_project":
        return "No project"
    return _slug_display_names().get(slug) or slug


def load_folders(project_root: str | None = None) -> list[ChatFolder]:
    if _use_db():
        rows = _repo().folders_get(_project_id(project_root))
        folders = [ChatFolder.from_dict(f) for f in rows]
        folders, archive_added = ensure_archive_folder(folders)
        if archive_added or _migrate_folder_sort_orders(folders):
            save_folders(folders, project_root)
        return _sort_folders(folders)
    path = _folders_path(project_root)
    if not path.is_file():
        write_json_atomic(path, {"folders": _default_folders()})
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("folders") if isinstance(data, dict) else data
        if not isinstance(raw, list):
            return []
        folders = [ChatFolder.from_dict(f) for f in raw if isinstance(f, dict)]
        folders, archive_added = ensure_archive_folder(folders)
        if archive_added or _migrate_folder_sort_orders(folders):
            save_folders(folders, project_root)
        return _sort_folders(folders)
    except (json.JSONDecodeError, OSError):
        return []


def folder_project_slug(folder_id: str) -> str:
    """The island a folder is stored under ("" when unknown): the sidebar shows Global Agents
    and other islands' folders too, so a folder action can't assume the open island."""
    if not _use_db() or not (folder_id or "").strip():
        return ""
    return _repo().folder_project_id(folder_id.strip()) or ""


def conversation_project_slug(conv_id: str) -> str:
    """The island a chat is stored under ("" when unknown or not on the database)."""
    if not _use_db() or not (conv_id or "").strip():
        return ""
    return _repo().conv_project_id(conv_id.strip()) or ""


def mark_folders_deleted(folder_ids: list[str], project_root: str | None = None) -> None:
    """Of these folders, the ones a delete removed: a stale folder list can't bring them back."""
    if not _use_db() or not folder_ids:
        return
    still = {f.id for f in load_folders(project_root)}
    _repo().folders_mark_deleted([fid for fid in folder_ids if fid and fid not in still])


def save_folders(folders: list[ChatFolder], project_root: str | None = None) -> None:
    if _use_db():
        _repo().folders_replace(_project_id(project_root), [f.to_dict() for f in folders])
        return
    write_json_atomic(_folders_path(project_root), {"folders": [f.to_dict() for f in folders]})


def create_folder(
    name: str, parent_id: str = "", project_root: str | None = None, *, group_hub_id: str = ""
) -> ChatFolder:
    if is_archive_folder_id(parent_id):
        raise ValueError("Cannot create folders inside Archive")
    folders = load_folders(project_root)
    parent_key = parent_id or ""
    siblings = [f for f in folders if (f.parent_id or "") == parent_key]
    next_order = max((f.sort_order for f in siblings), default=-1.0) + 1.0
    folder = ChatFolder(
        id=str(uuid.uuid4()),
        name=name.strip() or "Folder",
        parent_id=parent_id,
        sort_order=next_order,
    )
    if group_hub_id:
        folder.group_hub_id = group_hub_id
    folders.append(folder)
    save_folders(folders, project_root)
    return folder


def rename_folder(folder_id: str, name: str, project_root: str | None = None) -> None:
    assert_not_archive_folder(folder_id, action="rename")
    folders = load_folders(project_root)
    hub_id = ""
    new_name = ""
    for folder in folders:
        if folder.id == folder_id:
            folder.name = name.strip() or folder.name
            new_name = folder.name
            hub_id = (getattr(folder, "group_hub_id", None) or "").strip()
            break
    save_folders(folders, project_root)
    # Group folders share their name with the hub chat.
    if hub_id and new_name:
        hub = load_conversation(hub_id, project_root=project_root)
        if hub is not None and getattr(hub, "is_group", False):
            hub.title = new_name
            save_conversation(hub, project_root)


def ensure_group_folder_hubs(project_root: str | None = None) -> int:
    """Wrap legacy is_group chats in a folder hub so the sidebar shows one Group, not chat+folder."""
    folders = load_folders(project_root)
    linked = {(getattr(f, "group_hub_id", None) or "").strip() for f in folders}
    linked.discard("")
    migrated = 0
    # Full load: list_conversations() strips messages, and these get saved back.
    # (Rows: a metadata save leaves the transcript alone, so no bodies are read.)
    for conv in _load_all_conversations(project_root, include_messages=not _use_db()):
        if not getattr(conv, "is_group", False):
            continue
        if conv.id in linked:
            continue
        parent = (conv.folder_id or "").strip()
        # An unlinked hub in Archive is a tombstone from an older delete, not a
        # legacy group. Wrapping it in a folder resurrected deleted groups at the
        # root on the very next sidebar load.
        if is_archive_folder_id(parent):
            continue
        hub_folder = create_folder(conv.title or "Group", parent, project_root)
        conv.folder_id = hub_folder.id
        save_conversation(conv, project_root)
        folders = load_folders(project_root)
        for folder in folders:
            if folder.id == hub_folder.id:
                folder.group_hub_id = conv.id
                break
        save_folders(folders, project_root)
        linked.add(conv.id)
        migrated += 1
    return migrated


def folder_subtree_ids(folder_id: str, project_root: str | None = None) -> list[str]:
    """The folder plus every folder nested under it, parents before children."""
    folders = load_folders(project_root)
    if not any(f.id == folder_id for f in folders):
        return []
    children: dict[str, list[str]] = {}
    for folder in folders:
        children.setdefault(folder.parent_id or "", []).append(folder.id)
    out: list[str] = []
    seen: set[str] = set()
    queue = [folder_id]
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        out.append(current)
        queue.extend(children.get(current, []))
    return out


def group_hub_ids_in(folder_ids: list[str], project_root: str | None = None) -> list[str]:
    """Hub conversation ids for whichever of these folders are groups."""
    wanted = set(folder_ids)
    out: list[str] = []
    for folder in load_folders(project_root):
        if folder.id not in wanted:
            continue
        hub_id = (getattr(folder, "group_hub_id", None) or "").strip()
        if hub_id:
            out.append(hub_id)
    return out


def delete_folder(
    folder_id: str,
    project_root: str | None = None,
    *,
    archive_members: bool = False,
) -> list[str]:
    """Delete a folder. Returns group hub ids that moved to Archive.

    A group itself always goes to Archive. ``archive_members`` also sends its
    duckies (and nested groups) to Archive — for a plain folder too; otherwise those
    duckies stay active and nested groups move up to the deleted folder's parent.
    Plain folders left empty around an archived group go with it, and every removed
    folder is marked deleted so a stale folder list can't bring it back.
    """
    assert_not_archive_folder(folder_id, action="delete")
    folders = load_folders(project_root)
    target = next((f for f in folders if f.id == folder_id), None)
    if target is None:
        return []
    subtree = folder_subtree_ids(folder_id, project_root)
    archived = _remove_folder(target, folders, project_root, archive_members)
    if archived or archive_members:
        subtree += _drop_emptied_parents((target.parent_id or "").strip(), project_root)
    mark_folders_deleted(subtree, project_root)
    return archived


def _drop_emptied_parents(parent_id: str, project_root: str | None) -> list[str]:
    """Plain folders that held only what was just archived: removed, nearest first."""
    dropped: list[str] = []
    while parent_id and not is_archive_folder_id(parent_id):
        folders = load_folders(project_root)
        parent = next((f for f in folders if f.id == parent_id), None)
        if parent is None or (getattr(parent, "group_hub_id", None) or "").strip():
            break
        if any((f.parent_id or "") == parent_id for f in folders):
            break
        if any((c.folder_id or "") == parent_id for c in _load_all_conversations(project_root, include_messages=False)):
            break
        save_folders([f for f in folders if f.id != parent_id], project_root)
        dropped.append(parent_id)
        parent_id = (parent.parent_id or "").strip()
    return dropped


def _remove_folder(
    target: ChatFolder,
    folders: list[ChatFolder],
    project_root: str | None,
    archive_members: bool,
) -> list[str]:
    folder_id = target.id
    if not (getattr(target, "group_hub_id", None) or "").strip() and not archive_members:
        # Plain folder: its contents surface at the root rather than being deleted.
        for folder in folders:
            if folder.parent_id == folder_id:
                folder.parent_id = ""
        save_folders([f for f in folders if f.id != folder_id], project_root)
        # Full load: list_conversations() strips messages, so saving those back
        # would blank the chat history. (Rows: a metadata save leaves them alone.)
        for conv in _load_all_conversations(project_root, include_messages=not _use_db()):
            if conv.folder_id != folder_id:
                continue
            conv.folder_id = ""
            save_conversation(conv, project_root)
        return []

    all_convs = _load_all_conversations(project_root, include_messages=not _use_db())
    parent_id = (target.parent_id or "").strip()

    def _archive_hub(conv: Conversation) -> None:
        # Stay a group row in Archive. ensure_group_folder_hubs skips Archive,
        # so this does not come back as a sidebar folder until Return to active.
        conv.folder_id = ARCHIVE_FOLDER_ID
        conv.parent_conv_id = ""
        conv.leader_conv_id = ""
        save_conversation(conv, project_root)

    if not archive_members:
        hub_id = (getattr(target, "group_hub_id", None) or "").strip()
        for folder in folders:
            if folder.parent_id == folder_id:
                folder.parent_id = parent_id
        save_folders([f for f in folders if f.id != folder_id], project_root)
        for conv in all_convs:
            if hub_id and conv.id == hub_id:
                _archive_hub(conv)
                continue
            linked = bool(hub_id) and (conv.parent_conv_id or "").strip() == hub_id
            if conv.folder_id == folder_id:
                conv.folder_id = parent_id
                if linked:
                    conv.parent_conv_id = ""
                    conv.leader_conv_id = ""
                save_conversation(conv, project_root)
            elif linked:
                conv.parent_conv_id = ""
                conv.leader_conv_id = ""
                save_conversation(conv, project_root)
        return [hub_id] if hub_id else []

    # Whole subtree: nested groups and their duckies go to Archive with this one, and keep
    # their shape there — members stay linked to their group, a nested group to the group
    # around it — so Archive shows one group and Return to active / Delete permanently take
    # all of it.
    doomed_ids = set(folder_subtree_ids(folder_id, project_root))
    hub_ids = set(group_hub_ids_in(sorted(doomed_ids), project_root))
    folder_by_id = {f.id: f for f in folders}

    def _container_hub(fid: str) -> str:
        """The group a folder of this subtree belongs to: itself or its nearest group parent."""
        while fid in doomed_ids:
            hub = (getattr(folder_by_id.get(fid), "group_hub_id", None) or "").strip()
            if hub:
                return hub
            fid = (getattr(folder_by_id.get(fid), "parent_id", "") or "").strip()
        return ""

    outer_hub_of = {
        (getattr(f, "group_hub_id", None) or "").strip(): _container_hub((f.parent_id or "").strip())
        for f in folders
        if f.id in doomed_ids and (getattr(f, "group_hub_id", None) or "").strip()
    }
    save_folders([f for f in folders if f.id not in doomed_ids], project_root)

    by_parent: dict[str, list[Conversation]] = {}
    for conv in all_convs:
        by_parent.setdefault((conv.parent_conv_id or "").strip(), []).append(conv)

    members: list[Conversation] = []
    seen = set(hub_ids)
    queue = [
        conv
        for conv in all_convs
        if conv.id not in hub_ids
        and (conv.folder_id in doomed_ids or (conv.parent_conv_id or "").strip() in hub_ids)
    ]
    while queue:
        conv = queue.pop()
        if conv.id in seen:
            continue
        seen.add(conv.id)
        members.append(conv)
        queue.extend(by_parent.get(conv.id, []))

    for conv in members:
        if not (conv.parent_conv_id or "").strip() and conv.folder_id in doomed_ids:
            # A ducky that sat in the group's folder belongs to that group in Archive.
            conv.parent_conv_id = _container_hub(conv.folder_id)
        conv.folder_id = ARCHIVE_FOLDER_ID
        save_conversation(conv, project_root)

    for conv in all_convs:
        if conv.id in hub_ids:
            # The leader stays, for Return to active; a nested group points at its outer group.
            conv.folder_id = ARCHIVE_FOLDER_ID
            conv.parent_conv_id = outer_hub_of.get(conv.id, "")
            save_conversation(conv, project_root)
    return sorted(hub_ids)


def restore_archived_group(hub_id: str, project_root: str | None = None, parent_folder_id: str = "") -> list[str]:
    """Return an archived group to active with everything archived with it: the group gets
    a folder again, its duckies go back in it and its nested groups come back inside it.
    Returns the restored chat ids."""
    convs = _load_all_conversations(project_root, include_messages=not _use_db())
    by_id = {c.id: c for c in convs}
    hub = by_id.get(hub_id)
    if hub is None or not getattr(hub, "is_group", False):
        raise ValueError(f"Not an archived group: {hub_id!r}")
    archived_children: dict[str, list[Conversation]] = {}
    for conv in convs:
        if is_archive_folder_id(conv.folder_id) and conv.id != hub_id:
            archived_children.setdefault((conv.parent_conv_id or "").strip(), []).append(conv)
    restored: list[str] = []
    seen: set[str] = set()

    def _restore_chat(conv: Conversation, folder_id: str) -> None:
        if conv.id in seen:
            return
        seen.add(conv.id)
        conv.folder_id = folder_id
        save_conversation(conv, project_root)
        restored.append(conv.id)
        for child in archived_children.get(conv.id, []):
            _restore_chat(child, folder_id)  # its sub-agent chats come along

    def _restore_group(group: Conversation, parent_id: str) -> None:
        if group.id in seen:
            return
        seen.add(group.id)
        folder = create_folder(group.title or "Group", parent_id, project_root)
        rows = load_folders(project_root)
        for row in rows:
            if row.id == folder.id:
                row.group_hub_id = group.id
        save_folders(rows, project_root)
        group.folder_id = folder.id
        group.parent_conv_id = ""  # active nesting is the folder tree
        save_conversation(group, project_root)
        restored.append(group.id)
        for child in archived_children.get(group.id, []):
            if getattr(child, "is_group", False):
                _restore_group(child, folder.id)
            else:
                _restore_chat(child, folder.id)

    target = parent_folder_id if _folder_is_known(parent_folder_id, project_root) else ""
    _restore_group(hub, "" if is_archive_folder_id(target) else target)
    return restored


def island_for_conversation(
    conv_id: str, project_root: str | None = None
) -> tuple[list[Conversation], list[ChatFolder]]:
    """Chats and folders on the island that owns conv_id.

    All-projects mode files a group folder on the group's island. Listing the
    open island instead drops members that were invited from another project.
    """
    if _use_db():
        slug = (_repo().conv_project_id(conv_id) or "").strip()
        if slug:
            convs = [
                Conversation.from_dict(doc)
                for doc in _repo().conv_list(slug, with_messages=False)
            ]
            folders = [ChatFolder.from_dict(row) for row in _repo().folders_get(slug)]
            return convs, folders
    return list_conversations(project_root=project_root), load_folders(project_root)


def list_conversations(folder_id: str | None = None, project_root: str | None = None) -> list[Conversation]:
    _maybe_migrate_conversation_sort_orders(project_root)
    all_convs = _load_all_conversations(project_root, include_messages=False)
    out = [c for c in all_convs if folder_id is None or c.folder_id == folder_id]
    return _sort_conversations(out)


def list_all_conversation_metadata(project_root: str | None = None) -> list[Conversation]:
    """All chats for the sidebar — metadata only (messages stripped)."""
    return list_conversations(folder_id=None, project_root=project_root)


def list_conversations_for_file(
    file_path: str,
    project_root: str | None = None,
) -> list[Conversation]:
    target = norm_file_path(file_path)
    if not target:
        return []
    return _sort_conversations(
        [
            c
            for c in _load_all_conversations(project_root, include_messages=False)
            if norm_file_path(c.file_path) == target
        ]
    )


def remap_conversation_file_paths(
    from_path: str,
    to_path: str,
    project_root: str | None = None,
) -> int:
    """Update stored ducky file paths after a project file move/rename."""
    changed = 0
    for conv in _load_all_conversations(project_root, include_messages=not _use_db()):
        next_path = remap_conversation_file_path(conv.file_path, from_path, to_path)
        if next_path != norm_file_path(conv.file_path):
            conv.file_path = next_path
            save_conversation(conv, project_root, touch_updated=False)
            changed += 1
    return changed


def conversation_path(conv_id: str, project_root: str | None = None) -> Path:
    """Primary save path: folder layout conversation.json."""
    from frontend.ui_web.conversation_attachments import conversation_meta_path

    return conversation_meta_path(conv_id, _conversations_dir(project_root))


def _find_conversation_meta(conv_id: str, project_root: str | None = None) -> Path | None:
    primary = conversation_path(conv_id, project_root)
    if primary.is_file():
        return primary
    for meta in _iter_all_conversation_meta_paths():
        if meta.parent.name == conv_id:
            return meta
    return None


def conversation_project_slug(conv_id: str, project_root: str | None = None) -> str | None:
    """Return the stored owner, regardless of the project used to find the chat.

    An unknown chat returns None; a projectless chat belongs to ``_no_project``.
    """
    if _use_db():
        return _repo().conv_project_id(conv_id)
    meta = _find_conversation_meta(conv_id, project_root)
    return meta.parent.parent.parent.name if meta is not None else None


def load_conversation(conv_id: str, project_root: str | None = None) -> Conversation | None:
    """Load a globally unique chat ID; project_root is only a lookup hint."""
    if _use_db():
        queued = _queued(conv_id)
        if queued is not None:
            return _held(_from_queue(queued, with_messages=True))
        # Ids are globally unique — don't filter by the active island slug.
        doc = _repo().conv_get(conv_id, with_messages=True)
        return _held(Conversation.from_dict(doc)) if doc is not None else None
    meta = _find_conversation_meta(conv_id, project_root)
    if meta is None:
        return None
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return _held(Conversation.from_dict(data))
    except (json.JSONDecodeError, OSError):
        pass
    return None


def load_conversation_meta(conv_id: str, project_root: str | None = None) -> Conversation | None:
    """The chat for its names and settings; the row store skips its messages. A
    coordinator's chat can hold hundreds; parsing them all to read a title took tens of ms."""
    if _use_db():
        queued = _queued(conv_id)
        if queued is not None:
            return _held(_from_queue(queued, with_messages=False))
        doc = _repo().conv_get(conv_id, with_messages=False)
        return _held(Conversation.from_dict(doc)) if doc is not None else None
    return load_conversation(conv_id, project_root)


def conversation_title(conv_id: str, project_root: str | None = None) -> str:
    """The sidebar title only; never loads message bodies on the row store."""
    if _use_db():
        queued = _queued(conv_id)
        if queued is not None:
            return str(queued.title or "").strip()
        doc = _repo().conv_get(conv_id, with_messages=False)
        return str((doc or {}).get("title") or "").strip()
    conv = load_conversation(conv_id, project_root)
    return str(getattr(conv, "title", "") or "").strip() if conv is not None else ""


def sync_skill_snapshot(
    conv: Conversation, settings: Any, project_root: str | None = None
) -> bool:
    """Rebuild a frozen skill index when the packs on disk changed.

    Returns True when the prompt text actually changed. A conversation freezes
    its skill index at creation so pack toggles elsewhere cannot rewrite an
    in-flight chat; without this, a chat opened before an update never learns
    that a new subskill exists (bodies are always read live, the index is not).

    Rebuilds from the conversation's OWN selection, so per-chat enables and
    disables survive. The prompt cache is only invalidated when the text really
    changed — a body-only fix moves the revision but not the index, and costs
    no cache miss.
    """
    from backend.skills.store import (
        build_skill_prompt,
        resolve_conversation_selection,
        skills_revision,
    )

    if not str(getattr(conv, "skill_snapshot", "") or "").strip():
        return False  # no snapshot: the send path rebuilds from selection anyway
    rev = skills_revision()
    if not rev or rev == str(getattr(conv, "skill_snapshot_rev", "") or ""):
        return False
    text = build_skill_prompt(resolve_conversation_selection(conv, settings))
    if not text.strip():
        return False  # never blank out a working snapshot
    changed = text.strip() != conv.skill_snapshot.strip()
    conv.skill_snapshot = text
    conv.skill_snapshot_rev = rev
    if changed:
        from backend.agent.prompt_cache import invalidate_conv_cache

        invalidate_conv_cache(conv)
    save_conversation(conv, project_root, touch_updated=False)
    return changed


# What apply_ducky_config and the web tools set. A turn holds a copy loaded before the edit.
_DUCKY_SETTINGS = (
    "ducky_style",
    "ducky_name",
    "profile_id",
    "ducky_personality",
    "tts_voice",
    "tts_speed",
    "thinking_effort",
    "web_access_allowed",
    "web_access_denied",
    "web_fetch_urls",
)


def _keep_newer_settings(conv: Conversation, stored: dict[str, Any] | None) -> None:
    """Saving an older copy never puts back the voice, name or style the user just changed."""
    if not stored:
        return
    edited = float(stored.get("settings_edited") or 0.0)
    if edited <= float(conv.settings_edited or 0.0):
        return
    fresh = Conversation.from_dict({**stored, "id": conv.id, "messages": []})
    for name in _DUCKY_SETTINGS:
        setattr(conv, name, getattr(fresh, name))
    conv.settings_edited = edited


# Where a chat sits and what it's called. A running turn holds a copy loaded before a
# move into a group, a drag between folders or a rename; saving it must not undo them.
# Mute rides along: muting a running member must survive its turn's save.
_PLACEMENT = ("title", "folder_id", "sort_order", "parent_conv_id", "leader_conv_id", "muted")
_HELD_ATTR = "_placement_held"


def _held(conv: Conversation) -> Conversation:
    """Remember this copy's placement as loaded (see :func:`_keep_newer_placement`)."""
    held = {name: getattr(conv, name) for name in _PLACEMENT}
    held["settings_edited"] = conv.settings_edited
    setattr(conv, _HELD_ATTR, held)
    return conv


def _placement_changed(conv: Conversation) -> bool:
    """A new chat, a move, rename, mute or settings edit: other processes read these at
    once (D23), so such a save waits until it is written."""
    held = getattr(conv, _HELD_ATTR, None)
    if not held:
        return True
    if conv.settings_edited != held.get("settings_edited"):
        return True
    return any(getattr(conv, name) != held.get(name) for name in _PLACEMENT)


def _reload_if_stale(conv: Conversation) -> None:
    """A copy without the assistant rows another writer stored is given them, so its later
    saves build on them. A metadata stub (no messages) is never reloaded."""
    incoming = conv.messages or []
    if not incoming or any(_is_assistant(m) for m in incoming):
        return
    count, has_assistant = _repo().conv_message_summary(conv.id)
    if count and has_assistant and count != len(incoming):
        conv.messages = _repo().messages_get(conv.id)


def _keep_newer_placement(conv: Conversation, stored: dict[str, Any] | None) -> None:
    """A field this copy didn't change but someone else did since it was loaded keeps
    their value. Copies never loaded through load_conversation save as they are."""
    held = getattr(conv, _HELD_ATTR, None)
    if not held or not stored:
        return
    fresh = Conversation.from_dict({**stored, "id": conv.id, "messages": []})
    for name in _PLACEMENT:
        mine, theirs = getattr(conv, name), getattr(fresh, name)
        if mine == held.get(name) and theirs != mine:
            setattr(conv, name, theirs)


def _keep_stored_lanes(conv: Conversation, stored: dict[str, Any] | None) -> None:
    """A group roster's write lanes never go back to an older copy's (Oct 24 2026)."""
    if not getattr(conv, "is_group", False) or not isinstance(stored, dict):
        return
    from backend.workspace.lanes import keep_stored_lanes

    conv.group_members = keep_stored_lanes(
        getattr(conv, "group_members", None) or [], stored.get("group_members") or []
    )


def save_conversation(
    conv: Conversation, project_root: str | None = None, *, touch_updated: bool = True, wait: bool = False
) -> Future | None:
    """Row store: the save is queued for the chat-writer thread and its future returned.
    ``wait=True`` (and any save that creates, moves, renames or re-settings a chat) returns
    once it is written, so other processes see it at once."""
    with _conversation_lock(conv.id):
        # A self-rename is made through the bridge while this runner owns an older
        # conversation object. Never save its placeholder over the chosen name.
        if is_placeholder_title(conv.title):
            stored = load_conversation_meta(conv.id, project_root)
            if stored is not None and not is_placeholder_title(stored.title):
                conv.title = stored.title
        if touch_updated:
            conv.updated = time.time()
        if _use_db():
            wait = wait or _placement_changed(conv)
            queued = _queued(conv.id)
            if queued is None:
                newer = _repo().conv_if_settings_newer(conv.id, conv.settings_edited)
            else:
                newer_edit = float(queued.settings_edited or 0.0) > float(conv.settings_edited or 0.0)
                newer = queued.to_dict() if newer_edit else None
            _keep_newer_settings(conv, newer)
            held = getattr(conv, _HELD_ATTR, None)
            if held or getattr(conv, "is_group", False):
                stored_doc = queued.to_dict() if queued is not None else _repo().conv_get(conv.id, with_messages=False)
                if held:
                    _keep_newer_placement(conv, stored_doc)
                _keep_stored_lanes(conv, stored_doc)
            if queued is None:
                _reload_if_stale(conv)
            snapshot = _shallow(conv)
            snapshot.messages = list(conv.messages or [])
            if queued is not None and queued.messages and _keeps_stored_rows(snapshot.messages, queued.messages):
                # The queued copy is what the store will hold: a stub replacing it in the
                # queue (latest wins) must not drop its transcript.
                snapshot.messages = list(queued.messages)
            if snapshot.messages and _host_owned(conv.id):
                _refuse_host_messages(conv.id)
                snapshot.messages = []  # a metadata save; loads read the host's rows
            writer = _writer()
            fut = writer.submit(conv.id, (snapshot, project_root))
            _held(conv)
            if wait:
                # Write it now: waiting out the batch timer cost the caller 100 ms.
                writer.flush(conv.id, timeout=60.0)
                fut.result(timeout=60.0)
            return fut
        path = conversation_path(conv.id, project_root)
        home = _find_conversation_meta(conv.id, project_root)
        if home is not None:
            path = home
        path.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.perf_counter()
        incoming_msgs = getattr(conv, "messages", None) or []
        # list_conversations() strips messages. Saving that stub must not blank history.
        # A sidebar/open stub that kept only user prompts must not wipe a checkpointed turn.
        if path.is_file():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    _keep_newer_settings(conv, existing)
                    _keep_newer_placement(conv, existing)
                    _keep_stored_lanes(conv, existing)
                disk_msgs = existing.get("messages") if isinstance(existing, dict) else None
                if isinstance(disk_msgs, list) and disk_msgs:
                    if not incoming_msgs:
                        conv.messages = disk_msgs
                    elif _disk_has_assistant_incoming_does_not(incoming_msgs, disk_msgs):
                        conv.messages = disk_msgs
            except (json.JSONDecodeError, OSError):
                pass
        data = conv.to_dict()
        write_json_atomic(path, data)
        _held(conv)
        try:
            from frontend.perf_trace import trace

            payload = json.dumps(data, ensure_ascii=False, default=str)
            trace(
                "conv_save",
                conv.id,
                (time.perf_counter() - t0) * 1000.0,
                bytes=len(payload),
                message_count=len(getattr(conv, "messages", []) or []),
            )
        except Exception:
            pass


def _save_conversation_rows(conv: Conversation, project_root: str | None) -> dict[str, Any]:
    """ADR 0003: one row per message. A metadata stub (no messages) or a stub that
    lost the assistant row never blanks a stored transcript — same guard as the
    file store, without reading the transcript back. Runs on the chat-writer thread
    with a snapshot, so ``conv.messages`` is never reloaded here."""
    t0 = time.perf_counter()
    repo = _repo()
    project_id = repo.conv_project_id(conv.id) or _project_id(project_root)
    incoming = list(getattr(conv, "messages", None) or [])
    stored_count, stored_has_assistant = repo.conv_message_summary(conv.id)
    messages: list[dict[str, Any]] | None = incoming
    if not incoming:
        messages = None  # nothing to sync (a stub, or a new chat): a metadata save
    elif stored_count and stored_has_assistant and not any(
        isinstance(m, dict) and m.get("role") == "assistant" for m in incoming
    ):
        messages = None
    if messages is not None and _host_owned(conv.id):  # its turn moved to a host meanwhile
        _refuse_host_messages(conv.id)
        messages = None
    doc = conv.to_dict()
    if messages is None:  # a message save counts after the write (_stats_by_hash)
        current = repo.conv_get(conv.id, with_messages=False)
        if current is not None:
            doc["tool_call_count"] = current.get("tool_call_count", doc["tool_call_count"])
            doc["file_count"] = current.get("file_count", doc["file_count"])
    from frontend.ui_web.chat_writer import doc_key

    writer = _WRITER
    key = doc_key(project_id, doc)
    if writer is not None and writer.unchanged(conv.id, key, messages):
        return {"unchanged": 1}
    result = repo.conv_save(project_id, doc, messages=messages)
    if result.get("skipped"):
        _forget_saved(conv.id)
        return result  # the chat was deleted meanwhile; it stays deleted
    if messages is not None:
        stats = _stats_by_hash(conv.id, messages, list(result.pop("hashes", None) or []))
        conv.tool_call_count, conv.file_count = stats["tool_call_count"], stats["file_count"]
        if (doc["tool_call_count"], doc["file_count"]) != (conv.tool_call_count, conv.file_count):
            repo.conv_set_counts(conv.id, **stats)
            doc["tool_call_count"], doc["file_count"] = conv.tool_call_count, conv.file_count
            key = doc_key(project_id, doc)
    if writer is not None:
        writer.remember(conv.id, key)
    try:
        from frontend.perf_trace import trace

        trace(
            "conv_save",
            conv.id,
            (time.perf_counter() - t0) * 1000.0,
            bytes=int(result.get("bytes", 0)),
            message_count=len(messages) if messages is not None else stored_count,
            rows=int(result.get("inserted", 0)) + int(result.get("updated", 0)),
        )
    except Exception:
        pass
    return result


def all_available_tool_ids() -> list[str]:
    """Globally available tool-group ids (builtins + MCP plugins + UEFN agent plugins)."""
    from backend.agent.builtin_toolsets import get_enabled_builtin_group_ids
    from backend.mcp_plugins.store import get_enabled_plugin_ids
    from backend.uefn_plugins.host import ensure_plugins_loaded, uefn_agent_tool_rows

    ensure_plugins_loaded()
    ids = list(get_enabled_builtin_group_ids()) + list(get_enabled_plugin_ids())
    for row in uefn_agent_tool_rows():
        pid = str(row.get("id") or "").strip()
        if pid and pid not in ids:
            ids.append(pid)
    return ids


def create_conversation(
    settings: PanelSettings | None = None,
    folder_id: str = "",
    *,
    title: str = "",
    skill_snapshot: str = "",
    enabled_packs: list[str] | None = None,
    enabled_subskills: dict[str, list[str]] | None = None,
    enabled_skills: list[str] | None = None,
    disabled_packs: list[str] | None = None,
    ducky_style: str = "classic",
    ducky_name: str = "",
    profile_id: str = "",
    ducky_personality: str = "",
    tts_voice: str = "",
    tts_speed: float = 0.0,
    thinking_effort: str = "",
    file_path: str = "",
    tool_ids: list[str] | None = None,
    disabled_tool_ids: list[str] | None = None,
    model: str | None = None,
    provider: str | None = None,
    project_root: str | None = None,
    coding_agent: str | None = None,
    parent_conv_id: str = "",
    conv_id: str = "",
) -> Conversation:
    del enabled_packs, enabled_skills, tool_ids  # legacy pack/tool allowlists ignored
    now = time.time()
    s = settings or PanelSettings.load()
    root = project_root if project_root is not None else s.uefn_project_root
    from backend.skills.store import conversation_skill_text, merge_selection, skills_revision
    from backend.agent.coding_agents.base import normalize_coding_agent
    from backend.agent.thinking_effort import normalize_thinking_effort

    sel = merge_selection(disabled_packs=disabled_packs, enabled_subskills=enabled_subskills)
    snapshot = skill_snapshot or conversation_skill_text(
        disabled_packs=sel.disabled_packs,
        enabled_subskills=sel.enabled_subskills,
        skill_snapshot="",
        mutate=False,
    )
    snapshot_rev = skills_revision()
    agent = normalize_coding_agent(coding_agent or "ducky")
    model_value = (model or "").strip()
    provider_value = (provider or "").strip()
    conv = Conversation(
        id=(conv_id or "").strip() or str(uuid.uuid4()),
        folder_id=folder_id or "",
        title=(title.strip()[:120] if title.strip() else "New ducky"),
        created=now,
        updated=now,
        sort_order=_next_sort_order(folder_id or "", root),
        provider=provider_value,
        model=model_value,
        skill_snapshot=snapshot,
        skill_snapshot_rev=snapshot_rev,
        disabled_packs=list(sel.disabled_packs),
        enabled_packs=None,
        enabled_subskills=dict(sel.enabled_subskills) or None,
        enabled_skills=None,
        ducky_style=normalize_ducky_style(ducky_style),
        ducky_name=(ducky_name or "").strip(),
        profile_id=(profile_id or "").strip(),
        ducky_personality=(ducky_personality or "").strip(),
        tts_voice=(tts_voice or "").strip(),
        tts_speed=float(tts_speed or 0.0),
        thinking_effort=normalize_thinking_effort(thinking_effort),
        file_path=norm_file_path(file_path),
        coding_agent=agent,
        parent_conv_id=(parent_conv_id or "").strip(),
    )
    _apply_disabled_tool_ids_to_conv(conv, disabled_tool_ids or [])
    save_conversation(conv, root)
    return conv


def _next_sort_order(folder_id: str, project_root: str | None) -> float:
    if _use_db():
        return _repo().conv_max_sort_order(_project_id(project_root), folder_id) + 1.0
    return max((c.sort_order for c in list_conversations(folder_id, project_root)), default=-1.0) + 1.0


def _apply_tool_ids_to_conv(conv: Conversation, tool_ids: list[str]) -> None:
    """Legacy allowlist writer — prefer _apply_disabled_tool_ids_to_conv."""
    from backend.agent.builtin_toolsets import is_builtin_group
    from backend.mcp_plugins.store import load_plugin_manifest as load_mcp_manifest
    from backend.mcp_plugins.store import normalize_plugin_id
    from backend.uefn_plugins.host import is_uefn_agent_tool_plugin

    builtin_sel: list[str] = []
    plugin_sel: list[str] = []
    uefn_sel: list[str] = []
    for item in tool_ids:
        pid = normalize_plugin_id(str(item))
        if is_builtin_group(pid):
            if pid not in builtin_sel:
                builtin_sel.append(pid)
            continue
        if is_uefn_agent_tool_plugin(pid):
            if pid not in uefn_sel:
                uefn_sel.append(pid)
            continue
        if load_mcp_manifest(pid) is not None and pid not in plugin_sel:
            plugin_sel.append(pid)
    conv.builtin_toolsets = builtin_sel
    conv.mcp_plugins = plugin_sel
    conv.uefn_plugins = uefn_sel


def _apply_disabled_tool_ids_to_conv(conv: Conversation, disabled_tool_ids: list[str]) -> None:
    """Write effective tool groups = all globally available minus the deny-list."""
    from backend.uefn_plugins.host import uefn_agent_tool_rows

    denied = {str(x).strip() for x in disabled_tool_ids if str(x).strip()}
    effective = [tid for tid in all_available_tool_ids() if tid not in denied]
    _apply_tool_ids_to_conv(conv, effective)
    # No UEFN app-plugin denied → follow Store enable so newly enabled plugins
    # appear in current sessions without rewriting every chat.
    available_uefn = {str(r.get("id") or "") for r in uefn_agent_tool_rows() if r.get("id")}
    if not (available_uefn & denied):
        conv.uefn_plugins = None


def _iter_all_conversation_meta_paths() -> list[Path]:
    root = _chats_root()
    if not root.is_dir():
        return []
    return list(root.glob("*/conversations/*/conversation.json"))


def opt_in_uefn_plugin_all_chats(plugin_id: str) -> int:
    """Add ``plugin_id`` to every chat with an explicit uefn_plugins allowlist."""
    from backend.uefn_plugins.store import normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    n = 0
    if _use_db():
        repo = _repo()
        flush_chat_saves()
        _forget_saved()
        for project_id, data in repo.conv_list_all_projects(with_messages=False):
            uefn = data.get("uefn_plugins")
            if uefn is None or not isinstance(uefn, list):
                continue
            ids = [str(x) for x in uefn]
            if pid in ids:
                continue
            data["uefn_plugins"] = ids + [pid]
            data["prompt_cache_snapshot"] = None
            repo.conv_save(project_id, data, messages=None)
            n += 1
        return n
    for meta in _iter_all_conversation_meta_paths():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        uefn = data.get("uefn_plugins")
        if uefn is None or not isinstance(uefn, list):
            continue
        ids = [str(x) for x in uefn]
        if pid in ids:
            continue
        data["uefn_plugins"] = ids + [pid]
        data["prompt_cache_snapshot"] = None
        write_json_atomic(meta, data)
        n += 1
    return n


def invalidate_all_projects_conversation_caches() -> None:
    """Clear frozen prompt snapshots for every chat across all projects."""
    if _use_db():
        repo = _repo()
        flush_chat_saves()
        _forget_saved()
        for project_id, data in repo.conv_list_all_projects(with_messages=False):
            if data.get("prompt_cache_snapshot") is None:
                continue
            data["prompt_cache_snapshot"] = None
            repo.conv_save(project_id, data, messages=None)
        return
    for meta in _iter_all_conversation_meta_paths():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict) or data.get("prompt_cache_snapshot") is None:
            continue
        data["prompt_cache_snapshot"] = None
        write_json_atomic(meta, data)


def apply_ducky_config(
    conv_id: str,
    *,
    ducky_style: str,
    ducky_name: str | None = None,
    profile_id: str | None = None,
    ducky_personality: str = "",
    tts_voice: str | None = None,
    tts_speed: float | None = None,
    thinking_effort: str | None = None,
    title: str | None = None,
    enabled_packs: list[str] | None = None,
    enabled_subskills: dict[str, list[str]] | None = None,
    tool_ids: list[str] | None = None,
    disabled_packs: list[str] | None = None,
    disabled_tool_ids: list[str] | None = None,
    project_root: str | None = None,
) -> None:
    del enabled_packs, tool_ids  # legacy allowlists ignored
    conv = load_conversation(conv_id, project_root)
    if not conv:
        raise ValueError(f"Conversation not found: {conv_id}")
    conv.ducky_style = normalize_ducky_style(ducky_style)
    if ducky_name is not None:
        conv.ducky_name = (ducky_name or "").strip()
    if profile_id is not None:
        conv.profile_id = (profile_id or "").strip()
    conv.ducky_personality = (ducky_personality or "").strip()
    if tts_voice is not None:
        conv.tts_voice = (tts_voice or "").strip()
    if tts_speed is not None:
        conv.tts_speed = float(tts_speed or 0.0)
    if thinking_effort is not None:
        from backend.agent.thinking_effort import normalize_thinking_effort

        conv.thinking_effort = normalize_thinking_effort(thinking_effort)
    if title is not None:
        text = title.strip()
        if text:
            conv.title = text[:120]
    if disabled_packs is not None or enabled_subskills is not None:
        next_disabled = (
            disabled_packs
            if disabled_packs is not None
            else list(getattr(conv, "disabled_packs", None) or [])
        )
        next_subs = (
            enabled_subskills
            if enabled_subskills is not None
            else (getattr(conv, "enabled_subskills", None) or {})
        )
        set_conversation_skill_selection(
            conv_id,
            enabled_packs=[],  # unused — disabled_packs wins via helper below
            enabled_subskills=next_subs if isinstance(next_subs, dict) else {},
            project_root=project_root,
            disabled_packs=next_disabled,
        )
        conv = load_conversation(conv_id, project_root)
        if not conv:
            return
    if disabled_tool_ids is not None:
        _apply_disabled_tool_ids_to_conv(conv, disabled_tool_ids)
    from backend.agent.prompt_cache import invalidate_conv_cache

    invalidate_conv_cache(conv)
    conv.settings_edited = time.time()
    save_conversation(conv, project_root, touch_updated=False)


def ensure_conversation_file_path(
    conv_id: str,
    file_path: str,
    project_root: str | None = None,
) -> None:
    path = norm_file_path(file_path)
    if not path:
        return
    conv = load_conversation(conv_id, project_root)
    if not conv or conv.file_path:
        return
    conv.file_path = path
    save_conversation(conv, project_root, touch_updated=False)


def set_conversation_enabled_skills(
    conv_id: str,
    filenames: list[str],
    project_root: str | None = None,
) -> list[str]:
    """Legacy: treat filenames as an allowlist → convert to deny complementary (unused)."""
    del filenames
    packs = set_conversation_disabled_packs(conv_id, [], project_root=project_root)
    return packs


def set_conversation_disabled_packs(
    conv_id: str,
    disabled_packs: list[str],
    project_root: str | None = None,
) -> list[str]:
    conv = load_conversation(conv_id, project_root)
    existing_subs = (
        dict(conv.enabled_subskills)
        if conv and isinstance(getattr(conv, "enabled_subskills", None), dict)
        else {}
    )
    _packs, _subs = set_conversation_skill_selection(
        conv_id,
        enabled_packs=[],
        enabled_subskills=existing_subs,
        project_root=project_root,
        disabled_packs=disabled_packs,
    )
    del _packs, _subs
    from backend.skills.store import merge_selection

    return merge_selection(disabled_packs=disabled_packs).disabled_packs


def set_conversation_skill_selection(
    conv_id: str,
    enabled_packs: list[str],
    enabled_subskills: dict[str, list[str]],
    project_root: str | None = None,
    *,
    disabled_packs: list[str] | None = None,
) -> tuple[list[str], dict[str, list[str]]]:
    """Persist pack deny-list + optional per-pack subskill allowlists."""
    from backend.skills.store import conversation_skill_text, merge_selection

    conv = load_conversation(conv_id, project_root)
    if not conv:
        return [], {}
    if disabled_packs is None:
        sel = merge_selection(
            enabled_packs=enabled_packs,
            enabled_subskills=enabled_subskills,
        )
    else:
        sel = merge_selection(
            disabled_packs=disabled_packs,
            enabled_subskills=enabled_subskills,
        )
    conv.disabled_packs = list(sel.disabled_packs)
    conv.enabled_packs = None
    conv.enabled_subskills = dict(sel.enabled_subskills) or None
    conv.enabled_skills = None
    conv.skill_snapshot = conversation_skill_text(
        disabled_packs=sel.disabled_packs,
        enabled_subskills=sel.enabled_subskills,
        skill_snapshot="",
        mutate=False,
    )
    from backend.skills.store import skills_revision

    conv.skill_snapshot_rev = skills_revision()
    from backend.agent.prompt_cache import invalidate_conv_cache

    invalidate_conv_cache(conv)
    save_conversation(conv, project_root, touch_updated=False)
    return sel.enabled_packs, sel.enabled_subskills


def invalidate_all_conversation_caches(project_root: str | None = None) -> None:
    """Clear frozen prompt snapshots for every chat in the project."""
    from backend.agent.prompt_cache import invalidate_conv_cache

    for conv in _load_all_conversations(project_root, include_messages=not _use_db()):
        invalidate_conv_cache(conv)
        save_conversation(conv, project_root, touch_updated=False)


def set_conversation_ducky_style(
    conv_id: str,
    ducky_style: str,
    ducky_personality: str | None = None,
    project_root: str | None = None,
) -> None:
    conv = load_conversation(conv_id, project_root)
    if conv:
        conv.ducky_style = normalize_ducky_style(ducky_style)
        if ducky_personality is not None:
            conv.ducky_personality = (ducky_personality or "").strip()
        from backend.agent.prompt_cache import invalidate_conv_cache

        invalidate_conv_cache(conv)
        save_conversation(conv, project_root, touch_updated=False)


def conversation_descendant_ids(conv_id: str, project_root: str | None = None) -> list[str]:
    """Return every persisted child/grandchild chat, cycle-safe and parent-first."""
    children: dict[str, list[str]] = {}
    if _use_db():
        home = _repo().conv_project_id(conv_id) or _project_id(project_root)
        children = _repo().conv_ids_by_parent(home)
    else:
        home_meta = _find_conversation_meta(conv_id, project_root)
        slug = home_meta.parent.parent.parent.name if home_meta is not None else _project_id(project_root)
        for conv in _load_conversations_for_slug(slug):
            parent = (conv.parent_conv_id or "").strip()
            if parent:
                children.setdefault(parent, []).append(conv.id)
    out: list[str] = []
    seen = {conv_id}
    queue = list(children.get(conv_id, []))
    while queue:
        child_id = queue.pop(0)
        if child_id in seen:
            continue
        seen.add(child_id)
        out.append(child_id)
        queue.extend(children.get(child_id, []))
    return out


def delete_conversation(conv_id: str, project_root: str | None = None) -> None:
    from frontend.ui_web.conversation_attachments import conversation_dir

    # A spawned chat is owned by its parent. Permanently deleting the parent must
    # not leave orphaned sub-agent conversations behind.
    ids = [conv_id, *conversation_descendant_ids(conv_id, project_root)]
    try:
        from backend.tools.panel.permission_prompt import forget_chat
    except Exception:
        forget_chat = None
    for target_id in reversed(ids):
        if _use_db():
            flush_chat_saves(target_id)
            _repo().conv_delete(target_id)
            _forget_saved(target_id)
        if forget_chat is not None:
            forget_chat(target_id)  # its "Allow everything" must not outlive it
        try:
            from backend.sandbox import paths as sandbox_paths

            if sandbox_paths.exists(target_id):
                from backend.sandbox import service as sandbox_service

                sandbox_service.clear(target_id)  # its sandbox, programs and worktrees go too
        except Exception:
            pass
        conv_folder = conversation_dir(target_id, project_root, _conversations_dir(project_root))
        if not conv_folder.is_dir():
            meta = _find_conversation_meta(target_id, project_root)
            if meta is not None:
                conv_folder = meta.parent
        if conv_folder.is_dir():
            try:
                shutil.rmtree(conv_folder)
            except OSError:
                pass
        release_conversation_lock(target_id)


# The sidebar auto-numbers fresh duckies ("NewDucky1", "New ducky2"), so the
# trailing digits have to count as unnamed too.
_PLACEHOLDER_RE = re.compile(
    r"^(new\s*ducky|new\s*chat|ducky|chat|group|sub-?agent)\s*\d*$", re.IGNORECASE
)


def is_placeholder_title(title: str) -> bool:
    """True when the chat still carries an auto-generated name the user never chose."""
    text = (title or "").strip()
    return not text or bool(_PLACEHOLDER_RE.match(text))


def auto_title(conv: Conversation, title: str, project_root: str | None = None) -> bool:
    """Apply ``title`` only while the chat still has a placeholder name. Returns True when set."""
    if not is_placeholder_title(conv.title):
        # Named spawn / invite / rename already set a real title — keep it.
        return False
    text = (title or "").strip().replace("\n", " ")
    if not text:
        return False
    conv.title = text[:60] + ("…" if len(text) > 60 else "")
    save_conversation(conv, project_root)
    return True


def retitle_if_unchanged(
    conv_id: str, expected: str, title: str, project_root: str | None = None
) -> bool:
    """Compare-and-swap the title so a background rename never overwrites a manual one."""
    text = (title or "").strip()
    if not text:
        return False
    with _conversation_lock(conv_id):
        fresh = load_conversation(conv_id, project_root)
        if fresh is None or (fresh.title or "").strip() != expected:
            return False
        fresh.title = text[:120]
        save_conversation(fresh, project_root, touch_updated=False)
    return True


def append_message(conv: Conversation, message: dict[str, Any], project_root: str | None = None) -> None:
    with _conversation_lock(conv.id):
        fresh = load_conversation(conv.id, project_root)
        if fresh is not None:
            conv.messages = list(fresh.messages)
            conv.updated = fresh.updated
        conv.messages.append(message)
        # A new row (a prompt, a steer, a reply): other readers (search, other processes)
        # look at the store right away, so it is written before this returns.
        save_conversation(conv, project_root, touch_updated=True, wait=True)


KILLED_TURN_ERROR = "Closed while the agent was still going."


def _disk_has_assistant_incoming_does_not(incoming: list[Any], disk: list[Any]) -> bool:
    def has_asst(msgs: list[Any]) -> bool:
        return any(isinstance(m, dict) and m.get("role") == "assistant" for m in msgs)

    return has_asst(disk) and not has_asst(incoming)


def upsert_in_flight_assistant(
    conv: Conversation,
    message: dict[str, Any],
    *,
    run_id: str,
    project_root: str | None = None,
) -> None:
    """Replace the last assistant for ``run_id``, or append if this run has none.

    Mid-turn checkpoints and the final emit share one row so a hard kill cannot
    leave a stack of partial assistants, and a finished turn does not duplicate.
    """
    rid = (run_id or "").strip()
    payload = dict(message)
    if rid:
        payload["run_id"] = rid
    if _use_db():
        with _conversation_lock(conv.id):
            _upsert_run_row(conv, payload, rid, project_root)
        return
    with _conversation_lock(conv.id):
        fresh = load_conversation(conv.id, project_root)
        if fresh is not None:
            conv.messages = list(fresh.messages)
            conv.updated = fresh.updated
        msgs = list(conv.messages or [])
        idx = -1
        if rid:
            for i in range(len(msgs) - 1, -1, -1):
                m = msgs[i]
                if not isinstance(m, dict):
                    continue
                if m.get("role") == "user":
                    break
                if m.get("role") == "assistant" and str(m.get("run_id") or "") == rid:
                    idx = i
                    break
        if idx >= 0:
            msgs[idx] = payload
        else:
            msgs.append(payload)
        conv.messages = msgs
        save_conversation(conv, project_root, touch_updated=True)


def _run_row_index(msgs: list[Any], rid: str) -> int:
    """Index of the run's assistant after the last user message, or -1."""
    for i in range(len(msgs) - 1, -1, -1):
        m = msgs[i]
        if not isinstance(m, dict):
            continue
        if m.get("role") == "user":
            break
        if rid and m.get("role") == "assistant" and str(m.get("run_id") or "") == rid:
            return i
    return -1


def _upsert_run_row(conv: Conversation, payload: dict[str, Any], rid: str, project_root: str | None) -> None:
    """Row store: write only this turn's row. ``conv.messages`` is patched the same way;
    when it no longer matches the stored chat (someone else added a row) it is reloaded,
    so a later full save of this copy never writes over their rows."""
    msgs = list(conv.messages or [])
    idx = _run_row_index(msgs, rid)
    if idx >= 0:
        msgs[idx] = payload
    else:
        idx = len(msgs)
        msgs.append(payload)
    now = time.time()
    flush_chat_saves(conv.id)  # a queued older copy must not land over this row
    result = _repo().upsert_run_message(
        conv.id, rid, payload, updated=now, stats=_stats_for(msgs)
    )
    if result.get("skipped"):
        return
    conv.updated = now
    if result.get("seq") == idx and result.get("count") == len(msgs):
        conv.messages = msgs
        return
    fresh = load_conversation(conv.id, project_root)
    if fresh is not None:
        conv.messages = list(fresh.messages)


def heal_usage_orphaned_turn(
    conv: Conversation,
    *,
    project_root: str | None = None,
    sent_before: float | None = None,
) -> bool:
    """Usage was saved mid-run but the assistant row never landed (embedded Ducky/Ollama).

    *sent_before*: only a prompt sent before then can be an orphan (a turn sent since
    Ducky started may still be running)."""
    last: dict[str, Any] | None = None
    for m in reversed(conv.messages or []):
        if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
            last = m
            break
    if last is None or last.get("role") != "user":
        return False
    usage = getattr(conv, "token_usage", None)
    calls = usage.get("calls") if isinstance(usage, dict) else None
    if not isinstance(calls, list) or not calls:
        return False
    user_ts = float(last.get("ts") or 0)
    if sent_before is not None and user_ts >= sent_before:
        return False
    if not any(isinstance(c, dict) and float(c.get("ts") or 0) > user_ts for c in calls):
        return False
    upsert_in_flight_assistant(
        conv,
        {
            "role": "assistant",
            "content": "",
            "text": "",
            "ts": time.time(),
            "incomplete": True,
            "error": KILLED_TURN_ERROR,
        },
        run_id=f"heal-{conv.id}",
        project_root=project_root,
    )
    return True


def heal_usage_orphaned_turns_for_project(
    project_root: str | None = None, *, sent_before: float | None = None
) -> list[str]:
    """Boot: persist a killed-turn stub when token_usage outran the transcript.

    Returns the ids of the chats it healed."""
    healed: list[str] = []
    for stub in list_conversations(project_root=project_root):
        usage = getattr(stub, "token_usage", None)
        calls = usage.get("calls") if isinstance(usage, dict) else None
        if not isinstance(calls, list) or not calls:
            continue
        conv = load_conversation(stub.id, project_root)
        if conv is None:
            continue
        if heal_usage_orphaned_turn(conv, project_root=project_root, sent_before=sent_before):
            healed.append(conv.id)
    return healed


def heal_killed_coding_turn(
    conv_id: str,
    run_id: str,
    *,
    project_root: str | None = None,
) -> bool:
    """If a dead run left only the user prompt, persist an interrupted assistant row."""
    cid = (conv_id or "").strip()
    rid = (run_id or "").strip()
    if not cid or not rid:
        return False
    conv = load_conversation(cid, project_root)
    if conv is None:
        return False
    last: dict[str, Any] | None = None
    for m in reversed(conv.messages or []):
        if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
            last = m
            break
    if last is None:
        return False
    if last.get("role") == "assistant":
        if str(last.get("run_id") or "") != rid or not last.get("incomplete"):
            return False
        if str(last.get("error") or "").strip():
            return False
        last = dict(last)
        last["error"] = KILLED_TURN_ERROR
        upsert_in_flight_assistant(conv, last, run_id=rid, project_root=project_root)
        return True
    if last.get("role") != "user":
        return False
    upsert_in_flight_assistant(
        conv,
        {
            "role": "assistant",
            "content": "",
            "text": "",
            "ts": time.time(),
            "coding_agent": (getattr(conv, "coding_agent", "") or "").strip(),
            "run_id": rid,
            "incomplete": True,
            "error": KILLED_TURN_ERROR,
        },
        run_id=rid,
        project_root=project_root,
    )
    return True


def rename_conversation(conv_id: str, title: str, project_root: str | None = None) -> None:
    conv = load_conversation(conv_id, project_root)
    if conv is None:
        raise ValueError(f"Conversation not found: {conv_id!r}")
    text = title.strip()
    if not text:
        raise ValueError("title must not be empty")
    conv.title = text[:120]
    save_conversation(conv, project_root)


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


def _folder_subtree(folders: list[ChatFolder], root_ids: list[str]) -> list[ChatFolder]:
    by_id = {folder.id: folder for folder in folders}
    by_parent: dict[str, list[ChatFolder]] = {}
    for folder in folders:
        by_parent.setdefault(folder.parent_id or "", []).append(folder)
    out: list[ChatFolder] = []
    seen: set[str] = set()
    queue = [fid for fid in root_ids if fid in by_id and not is_archive_folder_id(fid)]
    while queue:
        fid = queue.pop(0)
        if fid in seen or fid not in by_id:
            continue
        seen.add(fid)
        folder = by_id[fid]
        out.append(folder)
        queue.extend(child.id for child in by_parent.get(fid, []))
    return out


def move_chats_to_project(
    conv_ids: list[str],
    folder_ids: list[str],
    target_slug: str,
    folder_id: str = "",
) -> None:
    """Move duckies and group folders onto another island. Validates before any write."""
    target = (target_slug or "").strip()
    if not target:
        raise ValueError("target project is required")
    dest_folder = (folder_id or "").strip()
    if is_archive_folder_id(dest_folder):
        raise ValueError("Cannot move into Archive")

    folders_by_slug = {slug: list(rows) for slug, rows in iter_folders_by_project()}
    wanted_folders = [fid for fid in folder_ids if (fid or "").strip() and not is_archive_folder_id(fid)]
    moved_folders: list[tuple[str, ChatFolder]] = []
    seen_folder_ids: set[str] = set()
    for fid in wanted_folders:
        home = next((slug for slug, rows in folders_by_slug.items() if any(row.id == fid for row in rows)), "")
        if not home:
            raise ValueError(f"Unknown folder: {fid}")
        for folder in _folder_subtree(folders_by_slug[home], [fid]):
            if folder.id in seen_folder_ids:
                continue
            seen_folder_ids.add(folder.id)
            moved_folders.append((home, folder))

    moved_folder_ids = {folder.id for _, folder in moved_folders}
    if dest_folder and dest_folder in moved_folder_ids:
        raise ValueError("Cannot move a folder into itself")
    target_folder_ids = {row.id for row in folders_by_slug.get(target, [])}
    if dest_folder and dest_folder not in target_folder_ids:
        raise ValueError(f"Unknown folder: {dest_folder}")
    clash = moved_folder_ids & target_folder_ids
    if clash:
        raise ValueError(f"Target already has folder: {next(iter(clash))}")

    conv_home: dict[str, str] = {}
    convs_by_slug: dict[str, list[Conversation]] = {}
    for slug, conv in iter_conversations_by_project():
        conv_home[conv.id] = slug
        convs_by_slug.setdefault(slug, []).append(conv)

    wanted_convs = [cid for cid in conv_ids if (cid or "").strip()]
    for cid in wanted_convs:
        if cid not in conv_home:
            raise ValueError(f"Unknown conversation: {cid}")

    move_conv_ids: list[str] = []
    seen_convs: set[str] = set()

    def _add_conv(cid: str) -> None:
        if not cid or cid in seen_convs or cid not in conv_home:
            return
        seen_convs.add(cid)
        move_conv_ids.append(cid)
        for child_id in conversation_descendant_ids(cid):
            _add_conv(child_id)

    for _, folder in moved_folders:
        hub = (folder.group_hub_id or "").strip()
        if hub:
            _add_conv(hub)
    for slug, rows in convs_by_slug.items():
        del slug
        for conv in rows:
            if (conv.folder_id or "") in moved_folder_ids:
                _add_conv(conv.id)
    for cid in wanted_convs:
        _add_conv(cid)

    # Chats already on the target stay put. A folder already there is not moved again.
    move_conv_ids = [cid for cid in move_conv_ids if conv_home[cid] != target]
    moved_folders = [(slug, folder) for slug, folder in moved_folders if slug != target]
    if not move_conv_ids and not moved_folders:
        return

    for slug, folder in moved_folders:
        parent = folder.parent_id or ""
        if parent not in moved_folder_ids:
            folder.parent_id = dest_folder

    def _dest_folder_for(cid: str) -> str:
        current = _stored_folder_id(cid, convs_by_slug, conv_home)
        if current in moved_folder_ids:
            return current
        return dest_folder

    relocated: list[tuple[Path, Path]] = []
    try:
        for cid in move_conv_ids:
            src = _conversation_disk_dir(conv_home[cid], cid)
            dest = _conversation_disk_dir(target, cid)
            if not src.exists():
                continue
            if dest.exists():
                raise ValueError(f"Conversation directory already exists: {cid}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            relocated.append((dest, src))
        _commit_project_move(target, moved_folders, move_conv_ids, _dest_folder_for)
    except Exception:
        for dest, src in reversed(relocated):
            if dest.exists() and not src.exists():
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dest), str(src))
        raise


def _stored_folder_id(cid: str, convs_by_slug: dict[str, list[Conversation]], conv_home: dict[str, str]) -> str:
    slug = conv_home.get(cid) or ""
    for conv in convs_by_slug.get(slug, []):
        if conv.id == cid:
            return conv.folder_id or ""
    return ""


def _commit_project_move(
    target: str,
    moved_folders: list[tuple[str, ChatFolder]],
    move_conv_ids: list[str],
    dest_folder_for,
) -> None:
    if _use_db():
        flush_chat_saves()
        _forget_saved()
        _repo().move_to_project(
            target,
            [(source, folder.id, folder.parent_id or "") for source, folder in moved_folders],
            [(cid, dest_folder_for(cid)) for cid in move_conv_ids],
        )
        return

    for cid in move_conv_ids:
        path = _conversation_disk_dir(target, cid) / "conversation.json"
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data["folder_id"] = dest_folder_for(cid)
            write_json_atomic(path, data)
    by_source: dict[str, list[ChatFolder]] = {}
    for source, folder in moved_folders:
        by_source.setdefault(source, []).append(folder)
    for source, leaving in by_source.items():
        leaving_ids = {folder.id for folder in leaving}
        remain = [folder for folder in _load_folders_for_slug(source) if folder.id not in leaving_ids]
        _write_folders_slug(source, remain)
    if moved_folders:
        target_rows, _added = ensure_archive_folder(_load_folders_for_slug(target))
        target_rows.extend(folder for _, folder in moved_folders)
        _write_folders_slug(target, target_rows)


def _write_folders_slug(slug: str, folders: list[ChatFolder]) -> None:
    path = _chats_root() / slug / "folders.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, {"folders": [folder.to_dict() for folder in folders]})


_one_list_lock = threading.Lock()
_one_list_done: set[str] = set()


def _one_list_key() -> str:
    if _use_db():
        from backend.store import account_rows, db

        return f"{db.db_path()}|{account_rows.current()}"
    return str(_chats_root())


def merge_into_one_list() -> int:
    """Move every project's duckies, groups and folders into the one list, once per process
    and account. Nothing is deleted: folders keep their nesting and their duckies, Archive
    merges into one, and each project's rows go after the ones already in the list, the
    open project's first. Returns how many chats moved."""
    key = _one_list_key()
    if key in _one_list_done:
        return 0
    with _one_list_lock:
        if key in _one_list_done:
            return 0
        try:
            return _merge_into_one_list()
        finally:
            _one_list_done.add(key)  # a failed project is tried again on the next start


def _merge_into_one_list() -> int:
    import logging

    folders_by_slug = {slug: list(rows) for slug, rows in iter_folders_by_project()}
    convs_by_slug: dict[str, list[Conversation]] = {}
    for slug, conv in iter_conversations_by_project():
        convs_by_slug.setdefault(slug, []).append(conv)
    current = project_slug(PanelSettings.load().uefn_project_root or "")
    sources = [
        slug
        for slug in dict.fromkeys([current, *folders_by_slug, *convs_by_slug])
        if slug != ONE_LIST_SLUG
        and (convs_by_slug.get(slug) or any(not is_archive_folder_id(f.id) for f in folders_by_slug.get(slug, [])))
    ]
    if not sources:
        return 0
    load_folders("")  # the list's Archive folder exists before archived duckies arrive
    here_folders = [f for f in folders_by_slug.get(ONE_LIST_SLUG, []) if not is_archive_folder_id(f.id)]
    here_ids = {f.id for f in here_folders}
    tops = [f.sort_order for f in here_folders if (f.parent_id or "") not in here_ids]
    tops += [c.sort_order for c in convs_by_slug.get(ONE_LIST_SLUG, []) if not (c.folder_id or "")]
    next_order = max(tops, default=-1.0) + 1.0
    layout_folders: list[dict[str, Any]] = []
    layout_chats: list[dict[str, Any]] = []
    moved = 0
    for slug in sources:
        folders = [f for f in folders_by_slug.get(slug, []) if not is_archive_folder_id(f.id)]
        ids = {f.id for f in folders}
        convs = convs_by_slug.get(slug, [])
        archived = [c.id for c in convs if is_archive_folder_id(c.folder_id or "")]
        top_folders = sorted((f for f in folders if (f.parent_id or "") not in ids), key=lambda f: f.sort_order)
        top_convs = sorted(
            (c for c in convs if not is_archive_folder_id(c.folder_id or "") and (c.folder_id or "") not in ids),
            key=lambda c: c.sort_order,
        )
        try:
            if top_convs or top_folders:
                move_chats_to_project([c.id for c in top_convs], [f.id for f in top_folders], ONE_LIST_SLUG, "")
            if archived:
                _move_archived_to_one_list(slug, archived)
        except Exception:  # noqa: BLE001 - that project's rows stay where they are, whole
            logging.getLogger(__name__).exception("could not move the duckies of %s into the one list", slug)
            continue
        layout_folders += [{"id": f.id, "parent_id": "", "sort_order": next_order + i} for i, f in enumerate(top_folders)]
        layout_chats += [{"id": c.id, "folder_id": "", "sort_order": next_order + i} for i, c in enumerate(top_convs)]
        next_order += float(max(len(top_folders), len(top_convs)))
        moved += len(convs)
    if layout_folders or layout_chats:
        apply_sidebar_layout(folders=layout_folders, chats=layout_chats, project_root="")
    return moved


def _move_archived_to_one_list(slug: str, conv_ids: list[str]) -> None:
    """Archived duckies of ``slug`` go to the list's Archive (their disk folders too)."""
    relocated: list[tuple[Path, Path]] = []
    try:
        for cid in conv_ids:
            src = _conversation_disk_dir(slug, cid)
            dest = _conversation_disk_dir(ONE_LIST_SLUG, cid)
            if not src.exists() or dest.exists():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            relocated.append((dest, src))
        _commit_project_move(ONE_LIST_SLUG, [], conv_ids, lambda _cid: ARCHIVE_FOLDER_ID)
    except Exception:
        for dest, src in reversed(relocated):
            if dest.exists() and not src.exists():
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dest), str(src))
        raise


def adopt_group_project(conv: Conversation, group_id: str) -> None:
    """Put a member on the group's island.

    All-projects mode otherwise files the new chat under the open island, and
    the group folder (which lives on the group's island) never lists it.
    """
    if not _use_db():
        return
    gid = (group_id or "").strip()
    if not gid:
        return
    home = _repo().conv_project_id(gid)
    flush_chat_saves(conv.id)
    if not home or home == _repo().conv_project_id(conv.id):
        return
    _repo().conv_save(home, conv.to_dict(), messages=None)
    _forget_saved(conv.id)


def _folder_is_known(folder_id: str, project_root: str | None) -> bool:
    if not folder_id or is_archive_folder_id(folder_id):
        return True
    if folder_id in {f.id for f in load_folders(project_root)}:
        return True
    # All-projects: the group folder can live on another island.
    return any(folder.id == folder_id for _slug, folders in iter_folders_by_project() for folder in folders)


def move_conversation(conv_id: str, folder_id: str, project_root: str | None = None) -> None:
    conv = load_conversation(conv_id, project_root)
    if conv is None:
        raise ValueError(f"Conversation not found: {conv_id!r}")
    if not _folder_is_known(folder_id, project_root):
        raise ValueError(f"Unknown folder: {folder_id!r}")
    target_folder = folder_id or ""
    archiving = is_archive_folder_id(target_folder)
    restoring = is_archive_folder_id(conv.folder_id) and not archiving
    if getattr(conv, "is_group", False):
        # A group goes to Archive and comes back whole, with its duckies and nested groups.
        if restoring:
            restore_archived_group(conv_id, project_root, target_folder)
            return
        if archiving:
            own = next((f for f in load_folders(project_root) if (getattr(f, "group_hub_id", None) or "") == conv_id), None)
            if own is not None:
                delete_folder(own.id, project_root, archive_members=True)
                return
    if restoring and (conv.parent_conv_id or "").strip():
        parent = load_conversation(conv.parent_conv_id.strip(), project_root)
        if parent is not None and getattr(parent, "is_group", False) and is_archive_folder_id(parent.folder_id):
            conv.parent_conv_id = ""  # out of an archived group: a ducky on its own
    # Archive/restore the complete owned sub-agent tree. Otherwise children become
    # orphaned top-level chats and archived agents remain addressable.
    cascade = archiving or restoring
    ids = [conv_id, *conversation_descendant_ids(conv_id, project_root)] if cascade else [conv_id]
    by_id = {c.id: c for c in _load_all_conversations(project_root, include_messages=not _use_db())}
    by_id[conv.id] = conv
    for target_id in ids:
        target = by_id.get(target_id)
        if target is None:
            continue
        target.folder_id = target_folder
        save_conversation(target, project_root)


def apply_sidebar_layout(
    *,
    folders: list[dict[str, Any]],
    chats: list[dict[str, Any]],
    project_root: str | None = None,
) -> int:
    """Persist one project's folder order/nesting and chat positions.

    A window can show a stale tree: another window or an agent deleted a chat or a
    group, or moved it to another island. Rows for ids this project no longer has are
    skipped (never created), and a layout never moves a chat into or out of Archive —
    that is move_conversation's job — so a stale save can't bring anything back.
    Returns how many rows were skipped.
    """
    folder_updates = {
        str(row.get("id", "")): row for row in folders if isinstance(row, dict) and str(row.get("id") or "").strip()
    }
    chat_updates = {
        str(row.get("id", "")): row for row in chats if isinstance(row, dict) and str(row.get("id") or "").strip()
    }
    skipped = 0

    current_folders = load_folders(project_root)
    by_folder_id = _folder_by_id(current_folders)
    changed: dict[str, tuple[str, float]] = {}
    for folder_id, row in folder_updates.items():
        folder = by_folder_id.get(folder_id)
        if folder is None or is_archive_folder_id(folder_id):
            skipped += 1
            continue
        parent_id = str(row.get("parent_id", "") or "")
        if parent_id and (parent_id not in by_folder_id or is_archive_folder_id(parent_id)):
            skipped += 1
            continue
        if _would_create_cycle(current_folders, folder.id, parent_id):
            skipped += 1
            continue
        try:
            sort_order = float(row.get("sort_order", folder.sort_order))
        except (TypeError, ValueError):
            sort_order = folder.sort_order
        if (folder.parent_id or "") == parent_id and folder.sort_order == sort_order:
            continue
        folder.parent_id = parent_id
        folder.sort_order = sort_order
        changed[folder.id] = (parent_id, sort_order)

    if changed:
        # Re-read right before writing: a folder deleted meanwhile stays deleted, and
        # folders this layout did not touch keep whatever they have now.
        latest = load_folders(project_root)
        latest_ids = {folder.id for folder in latest}
        for folder in latest:
            update = changed.get(folder.id)
            if update is None:
                continue
            parent_id, sort_order = update
            if parent_id and parent_id not in latest_ids:
                continue
            if _would_create_cycle(latest, folder.id, parent_id):
                continue
            folder.parent_id = parent_id
            folder.sort_order = sort_order
        save_folders(latest, project_root)
        by_folder_id = _folder_by_id(latest)

    if chat_updates:
        all_convs = _load_all_conversations(project_root, include_messages=not _use_db())
        by_conv_id = {c.id: c for c in all_convs}
        here = _project_id(project_root)
        for conv_id, row in chat_updates.items():
            conv = by_conv_id.get(conv_id)
            if conv is None:
                skipped += 1
                continue
            current_folder = conv.folder_id or ""
            folder_id = str(row.get("folder_id", current_folder) or "")
            if is_archive_folder_id(current_folder) or is_archive_folder_id(folder_id):
                if folder_id != current_folder:
                    skipped += 1
                continue
            if folder_id and folder_id not in by_folder_id:
                skipped += 1
                continue
            try:
                sort_order = float(row.get("sort_order", conv.sort_order))
            except (TypeError, ValueError):
                sort_order = conv.sort_order
            if current_folder == folder_id and conv.sort_order == sort_order:
                continue
            # Deleted (or moved to another island) since the list above: saving the
            # copy we hold would bring it back.
            if conversation_project_slug(conv_id, project_root) != here:
                skipped += 1
                continue
            conv.folder_id = folder_id
            conv.sort_order = sort_order
            save_conversation(conv, project_root, touch_updated=False)

    from frontend.ui_web.group_orchestrator import reconcile_group_rosters_from_folders

    reconcile_group_rosters_from_folders(project_root)
    return skipped
