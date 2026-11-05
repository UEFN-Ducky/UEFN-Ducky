"""Group hubs: legacy hub folders, archiving and restoring a group's subtree, the island
a group lives on, and a chat's sub-agent descendants."""

from __future__ import annotations

from frontend.archive_folder import ARCHIVE_FOLDER_ID, is_archive_folder_id
from frontend.chat_store import ChatFolder, Conversation
from frontend.ui_web.chats import conversations as _convs
from frontend.ui_web.chats import folders as _folders
from frontend.ui_web.chats import saves as _saves
from frontend.ui_web.chats import store as _store


def ensure_group_folder_hubs(project_root: str | None = None) -> int:
    """Wrap legacy is_group chats in a folder hub so the sidebar shows one Group, not chat+folder."""
    folders = _folders.load_folders(project_root)
    linked = {(getattr(f, "group_hub_id", None) or "").strip() for f in folders}
    linked.discard("")
    migrated = 0
    # Full load: list_conversations() strips messages, and these get saved back.
    # (Rows: a metadata save leaves the transcript alone, so no bodies are read.)
    for conv in _convs._load_all_conversations(project_root, include_messages=not _store._use_db()):
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
        hub_folder = _folders.create_folder(conv.title or "Group", parent, project_root)
        conv.folder_id = hub_folder.id
        _saves.save_conversation(conv, project_root)
        folders = _folders.load_folders(project_root)
        for folder in folders:
            if folder.id == hub_folder.id:
                folder.group_hub_id = conv.id
                break
        _folders.save_folders(folders, project_root)
        linked.add(conv.id)
        migrated += 1
    return migrated


def group_hub_ids_in(folder_ids: list[str], project_root: str | None = None) -> list[str]:
    """Hub conversation ids for whichever of these folders are groups."""
    wanted = set(folder_ids)
    out: list[str] = []
    for folder in _folders.load_folders(project_root):
        if folder.id not in wanted:
            continue
        hub_id = (getattr(folder, "group_hub_id", None) or "").strip()
        if hub_id:
            out.append(hub_id)
    return out


def _remove_folder(
    target: ChatFolder,
    folders: list[ChatFolder],
    project_root: str | None,
    archive_members: bool,
) -> list[str]:
    save_conversation = _saves.save_conversation
    folder_id = target.id
    if not (getattr(target, "group_hub_id", None) or "").strip() and not archive_members:
        # Plain folder: its contents surface at the root rather than being deleted.
        for folder in folders:
            if folder.parent_id == folder_id:
                folder.parent_id = ""
        _folders.save_folders([f for f in folders if f.id != folder_id], project_root)
        # Full load: list_conversations() strips messages, so saving those back
        # would blank the chat history. (Rows: a metadata save leaves them alone.)
        for conv in _convs._load_all_conversations(project_root, include_messages=not _store._use_db()):
            if conv.folder_id != folder_id:
                continue
            conv.folder_id = ""
            save_conversation(conv, project_root)
        return []

    all_convs = _convs._load_all_conversations(project_root, include_messages=not _store._use_db())
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
        _folders.save_folders([f for f in folders if f.id != folder_id], project_root)
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
    doomed_ids = set(_folders.folder_subtree_ids(folder_id, project_root))
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
    _folders.save_folders([f for f in folders if f.id not in doomed_ids], project_root)

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
    from frontend.ui_web.chats.archive_stop import stop_archived

    stop_archived([*hub_ids, *(conv.id for conv in members)])
    return sorted(hub_ids)


def restore_archived_group(hub_id: str, project_root: str | None = None, parent_folder_id: str = "") -> list[str]:
    """Return an archived group to active with everything archived with it: the group gets
    a folder again, its duckies go back in it and its nested groups come back inside it.
    Returns the restored chat ids."""
    convs = _convs._load_all_conversations(project_root, include_messages=not _store._use_db())
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
        _saves.save_conversation(conv, project_root)
        restored.append(conv.id)
        for child in archived_children.get(conv.id, []):
            _restore_chat(child, folder_id)  # its sub-agent chats come along

    def _restore_group(group: Conversation, parent_id: str) -> None:
        if group.id in seen:
            return
        seen.add(group.id)
        folder = _folders.create_folder(group.title or "Group", parent_id, project_root)
        rows = _folders.load_folders(project_root)
        for row in rows:
            if row.id == folder.id:
                row.group_hub_id = group.id
        _folders.save_folders(rows, project_root)
        group.folder_id = folder.id
        group.parent_conv_id = ""  # active nesting is the folder tree
        _saves.save_conversation(group, project_root)
        restored.append(group.id)
        for child in archived_children.get(group.id, []):
            if getattr(child, "is_group", False):
                _restore_group(child, folder.id)
            else:
                _restore_chat(child, folder.id)

    target = parent_folder_id if _folders._folder_is_known(parent_folder_id, project_root) else ""
    _restore_group(hub, "" if is_archive_folder_id(target) else target)
    return restored


def island_for_conversation(
    conv_id: str, project_root: str | None = None
) -> tuple[list[Conversation], list[ChatFolder]]:
    """Chats and folders on the island that owns conv_id.

    All-projects mode files a group folder on the group's island. Listing the
    open island instead drops members that were invited from another project.
    """
    if _store._use_db():
        slug = (_store._repo().conv_project_id(conv_id) or "").strip()
        if slug:
            convs = [
                Conversation.from_dict(doc)
                for doc in _store._repo().conv_list(slug, with_messages=False)
            ]
            folders = [ChatFolder.from_dict(row) for row in _store._repo().folders_get(slug)]
            return convs, folders
    return _convs.list_conversations(project_root=project_root), _folders.load_folders(project_root)


def conversation_descendant_ids(conv_id: str, project_root: str | None = None) -> list[str]:
    """Return every persisted child/grandchild chat, cycle-safe and parent-first."""
    children: dict[str, list[str]] = {}
    if _store._use_db():
        home = _store._repo().conv_project_id(conv_id) or _store._project_id(project_root)
        children = _store._repo().conv_ids_by_parent(home)
    else:
        home_meta = _convs._find_conversation_meta(conv_id, project_root)
        slug = home_meta.parent.parent.parent.name if home_meta is not None else _store._project_id(project_root)
        for conv in _convs._load_conversations_for_slug(slug):
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


def adopt_group_project(conv: Conversation, group_id: str) -> None:
    """Put a member on the group's island.

    All-projects mode otherwise files the new chat under the open island, and
    the group folder (which lives on the group's island) never lists it.
    """
    if not _store._use_db():
        return
    gid = (group_id or "").strip()
    if not gid:
        return
    home = _store._repo().conv_project_id(gid)
    _saves.flush_chat_saves(conv.id)
    if not home or home == _store._repo().conv_project_id(conv.id):
        return
    _store._repo().conv_save(home, conv.to_dict(), messages=None)
    _saves._forget_saved(conv.id)
