"""Chat folders: the folder list, sort order, delete, moves between projects, the
one-list merge and sidebar layout saves."""

from __future__ import annotations

import json
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any

from frontend.archive_folder import (
    ARCHIVE_FOLDER_ID,
    assert_not_archive_folder,
    ensure_archive_folder,
    is_archive_folder_id,
)
from frontend.atomic_json import write_json_atomic
from frontend.chat_store import ChatFolder, Conversation
from frontend.settings import PanelSettings
from frontend.ui_web.chats import conversations as _convs
from frontend.ui_web.chats import groups as _groups
from frontend.ui_web.chats import saves as _saves
from frontend.ui_web.chats import store as _store


def _default_folders() -> list[dict[str, Any]]:
    return []


def _folder_sort_key(folder: ChatFolder) -> tuple[str, float, str]:
    return (folder.parent_id or "", folder.sort_order, folder.id)


def _sort_folders(folders: list[ChatFolder]) -> list[ChatFolder]:
    return sorted(folders, key=_folder_sort_key)


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


def _load_folders_for_slug(slug: str) -> list[ChatFolder]:
    if _store._use_db():
        return [ChatFolder.from_dict(f) for f in _store._repo().folders_get(slug)]
    path = _store._chats_root() / slug / "folders.json"
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


def iter_folders_by_project() -> list[tuple[str, list[ChatFolder]]]:
    if _store._use_db():
        return [
            (pid, [ChatFolder.from_dict(f) for f in rows])
            for pid, rows in _store._repo().folders_list_all_projects()
        ]
    return [(slug, _load_folders_for_slug(slug)) for slug in _store._iter_disk_project_slugs()]


def load_folders(project_root: str | None = None) -> list[ChatFolder]:
    if _store._use_db():
        rows = _store._repo().folders_get(_store._project_id(project_root))
        folders = [ChatFolder.from_dict(f) for f in rows]
        folders, archive_added = ensure_archive_folder(folders)
        if archive_added or _migrate_folder_sort_orders(folders):
            save_folders(folders, project_root)
        return _sort_folders(folders)
    path = _store._folders_path(project_root)
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
    if not _store._use_db() or not (folder_id or "").strip():
        return ""
    return _store._repo().folder_project_id(folder_id.strip()) or ""


def mark_folders_deleted(folder_ids: list[str], project_root: str | None = None) -> None:
    """Of these folders, the ones a delete removed: a stale folder list can't bring them back."""
    if not _store._use_db() or not folder_ids:
        return
    still = {f.id for f in load_folders(project_root)}
    _store._repo().folders_mark_deleted([fid for fid in folder_ids if fid and fid not in still])


def save_folders(folders: list[ChatFolder], project_root: str | None = None) -> None:
    if _store._use_db():
        _store._repo().folders_replace(_store._project_id(project_root), [f.to_dict() for f in folders])
        return
    write_json_atomic(_store._folders_path(project_root), {"folders": [f.to_dict() for f in folders]})


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
        hub = _convs.load_conversation(hub_id, project_root=project_root)
        if hub is not None and getattr(hub, "is_group", False):
            hub.title = new_name
            _saves.save_conversation(hub, project_root)


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
    archived = _groups._remove_folder(target, folders, project_root, archive_members)
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
        if any(
            (c.folder_id or "") == parent_id
            for c in _convs._load_all_conversations(project_root, include_messages=False)
        ):
            break
        save_folders([f for f in folders if f.id != parent_id], project_root)
        dropped.append(parent_id)
        parent_id = (parent.parent_id or "").strip()
    return dropped


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
    for slug, conv in _convs.iter_conversations_by_project():
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
        for child_id in _groups.conversation_descendant_ids(cid):
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
            src = _store._conversation_disk_dir(conv_home[cid], cid)
            dest = _store._conversation_disk_dir(target, cid)
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
    if _store._use_db():
        _saves.flush_chat_saves()
        _saves._forget_saved()
        _store._repo().move_to_project(
            target,
            [(source, folder.id, folder.parent_id or "") for source, folder in moved_folders],
            [(cid, dest_folder_for(cid)) for cid in move_conv_ids],
        )
        return

    for cid in move_conv_ids:
        path = _store._conversation_disk_dir(target, cid) / "conversation.json"
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
    path = _store._chats_root() / slug / "folders.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, {"folders": [folder.to_dict() for folder in folders]})


_one_list_lock = threading.Lock()
_one_list_done: set[str] = set()


def _one_list_key() -> str:
    if _store._use_db():
        from backend.store import account_rows, db

        return f"{db.db_path()}|{account_rows.current()}"
    return str(_store._chats_root())


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

    one_list = _store.ONE_LIST_SLUG
    folders_by_slug = {slug: list(rows) for slug, rows in iter_folders_by_project()}
    convs_by_slug: dict[str, list[Conversation]] = {}
    for slug, conv in _convs.iter_conversations_by_project():
        convs_by_slug.setdefault(slug, []).append(conv)
    current = _store.project_slug(PanelSettings.load().uefn_project_root or "")
    sources = [
        slug
        for slug in dict.fromkeys([current, *folders_by_slug, *convs_by_slug])
        if slug != one_list
        and (convs_by_slug.get(slug) or any(not is_archive_folder_id(f.id) for f in folders_by_slug.get(slug, [])))
    ]
    if not sources:
        return 0
    load_folders("")  # the list's Archive folder exists before archived duckies arrive
    here_folders = [f for f in folders_by_slug.get(one_list, []) if not is_archive_folder_id(f.id)]
    here_ids = {f.id for f in here_folders}
    tops = [f.sort_order for f in here_folders if (f.parent_id or "") not in here_ids]
    tops += [c.sort_order for c in convs_by_slug.get(one_list, []) if not (c.folder_id or "")]
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
                move_chats_to_project([c.id for c in top_convs], [f.id for f in top_folders], one_list, "")
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
            src = _store._conversation_disk_dir(slug, cid)
            dest = _store._conversation_disk_dir(_store.ONE_LIST_SLUG, cid)
            if not src.exists() or dest.exists():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            relocated.append((dest, src))
        _commit_project_move(_store.ONE_LIST_SLUG, [], conv_ids, lambda _cid: ARCHIVE_FOLDER_ID)
    except Exception:
        for dest, src in reversed(relocated):
            if dest.exists() and not src.exists():
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dest), str(src))
        raise


def _folder_is_known(folder_id: str, project_root: str | None) -> bool:
    if not folder_id or is_archive_folder_id(folder_id):
        return True
    if folder_id in {f.id for f in load_folders(project_root)}:
        return True
    # All-projects: the group folder can live on another island.
    return any(folder.id == folder_id for _slug, folders in iter_folders_by_project() for folder in folders)


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
        all_convs = _convs._load_all_conversations(project_root, include_messages=not _store._use_db())
        by_conv_id = {c.id: c for c in all_convs}
        here = _store._project_id(project_root)
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
            if _convs.conversation_project_slug(conv_id, project_root) != here:
                skipped += 1
                continue
            conv.folder_id = folder_id
            conv.sort_order = sort_order
            _saves.save_conversation(conv, project_root, touch_updated=False)

    from frontend.ui_web.group_orchestrator import reconcile_group_rosters_from_folders

    reconcile_group_rosters_from_folders(project_root)
    return skipped
