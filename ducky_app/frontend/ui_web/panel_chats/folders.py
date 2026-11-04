"""Sidebar folders and chat placement: list folders and chats, create, rename, move, archive
and delete them, and apply a dragged sidebar layout."""

from __future__ import annotations

import threading
from typing import Any

import frontend.ui_web.panel_api as _pa

# ponytail: one lock for group creates and folder deletes; folder saves replace the whole
# list, so two at once lost one's change. Per-project locks if they ever need to overlap.
_FOLDER_WRITE_LOCK = threading.RLock()

# Duckies made with no island open (the Global Agents folder).
GLOBAL_SLUG = "_no_project"


def _folder_sidebar_row(
    f: Any,
    *,
    project_slug: str = "",
    project_name: str = "",
) -> dict[str, str | float]:
    row: dict[str, str | float] = {
        "id": f.id,
        "name": f.name,
        "parent_id": f.parent_id,
        "sort_order": f.sort_order,
        "group_hub_id": getattr(f, "group_hub_id", "") or "",
    }
    if project_slug:
        row["project_slug"] = project_slug
        row["project_name"] = project_name or project_slug
    return row


def _one_list() -> None:
    """Duckies are one list, whatever project is selected; older per-project rows move
    into it the first time the sidebar loads."""
    from frontend.ui_web.chats.folders import merge_into_one_list

    try:
        merge_into_one_list()
    except Exception:  # noqa: BLE001 - the list still shows what is already in it
        pass


def list_folders(self, all_projects: bool = False) -> list[dict[str, str | float]]:
    """The one Duckies list's folders (``all_projects`` is ignored: there is one list)."""
    del all_projects
    self._one_list()
    _pa.ensure_group_folder_hubs()
    return [self._folder_sidebar_row(f) for f in _pa.load_folders()]


def list_conversations(self, folder_id: str) -> list[dict[str, str | float | int]]:
    return [
        self._conversation_sidebar_row(c)
        for c in _pa.list_conversations(folder_id)
    ]


def list_all_conversations(self, all_projects: bool = False) -> list[dict[str, str | float | int | bool]]:
    """Every ducky in the one list, in one call (metadata only) for the sidebar."""
    del all_projects
    self._one_list()
    convs = list(_pa.list_all_conversation_metadata())
    group_ids = {c.id for c in convs if getattr(c, "is_group", False)}
    return [self._conversation_sidebar_row(c, group_ids=group_ids) for c in convs]


def list_conversations_for_file(self, file_path: str) -> list[dict[str, str | float | int]]:
    return [
        self._conversation_sidebar_row(c)
        for c in _pa.list_conversations_for_file(file_path)
    ]


def _sidebar_context_tokens(c: Any) -> int:
    """Cheap context-window size for sidebar hover totals (no recompute)."""
    from frontend.ui_web.token_usage import resolve_context_window_tokens

    stats = getattr(c, "coding_agent_stats", None)
    stored = 0
    num_turns = 0
    if isinstance(stats, dict):
        stored = int(stats.get("context_tokens") or 0)
        num_turns = int(stats.get("num_turns") or 0)
    last: dict[str, Any] | None = None
    usage = getattr(c, "token_usage", None)
    if isinstance(usage, dict):
        calls = usage.get("calls")
        if isinstance(calls, list) and calls and isinstance(calls[-1], dict):
            last = calls[-1]
    if last is not None:
        return resolve_context_window_tokens(
            stored_context_tokens=stored,
            input_tokens=int(last.get("input_tokens") or 0),
            cache_read_tokens=int(last.get("cache_read_tokens") or 0),
            cache_write_tokens=int(last.get("cache_write_tokens") or 0),
            num_turns=num_turns,
        )
    return max(0, stored)


def _conversation_sidebar_row(
    c: Any,
    *,
    group_ids: set[str] | None = None,
    project_slug: str = "",
    project_name: str = "",
) -> dict[str, str | float | int | bool]:
    del group_ids  # retained for call-site compat; subagents retired
    parent_id = (getattr(c, "parent_conv_id", None) or "").strip()
    row: dict[str, str | float | int | bool] = {
        "id": c.id,
        "title": c.title,
        "sort_order": c.sort_order,
        "updated": c.updated,
        "ducky_style": c.ducky_style,
        "ducky_name": c.ducky_name or "",
        "profile_id": (getattr(c, "profile_id", None) or "").strip(),
        "ducky_personality": c.ducky_personality or "",
        "tts_voice": getattr(c, "tts_voice", None) or "",
        "tts_speed": float(getattr(c, "tts_speed", 0) or 0.0),
        "file_path": c.file_path,
        "model": c.model or "",
        "provider": c.provider or "",
        "coding_agent": getattr(c, "coding_agent", None) or "ducky",
        "thinking_effort": getattr(c, "thinking_effort", None) or "",
        "terminal_session_id": getattr(c, "terminal_session_id", None) or "",
        "folder_id": getattr(c, "folder_id", None) or "",
        "parent_conv_id": parent_id,
        "is_group": bool(getattr(c, "is_group", False)),
        "muted": bool(getattr(c, "muted", False)),
        # Subagents retired — group members nest under hubs, never parent-linked seats.
        "is_subagent": False,
        "leader_conv_id": (getattr(c, "leader_conv_id", None) or "").strip(),
        "group_members": list(getattr(c, "group_members", None) or []),
        "tool_call_count": int(getattr(c, "tool_call_count", 0) or 0),
        "file_count": int(getattr(c, "file_count", 0) or 0),
        "context_tokens": _pa.PanelApi._sidebar_context_tokens(c),
    }
    if project_slug:
        row["project_slug"] = project_slug
        row["project_name"] = project_name or project_slug
    return row


def create_folder(self, name: str, parent_id: str = "") -> dict[str, str]:
    f = _pa.create_folder(name, parent_id)
    return {"id": f.id, "name": f.name}


def _folder_root(self, folder_id: str) -> str | None:
    """Where a folder lives (None: the open island). The sidebar lists Global Agents and,
    with every island shown, other islands' folders too."""
    from frontend.ui_web.chats.folders import folder_project_slug

    slug = folder_project_slug(folder_id)
    try:
        return self._layout_project_root(slug) if slug else None
    except ValueError:
        return None


def rename_folder(self, folder_id: str, name: str) -> None:
    _pa.rename_folder(folder_id, name, self._folder_root(folder_id))


def delete_folder(self, folder_id: str, archive_members: bool = False) -> list[str]:
    """Delete a folder. Returns group hub ids moved to Archive.

    archive_members also archives duckies in the group. Otherwise they stay.
    """
    root = self._folder_root(folder_id)
    subtree = _pa.folder_subtree_ids(folder_id, root)
    # Stop runners that are about to leave the active tree.
    scope = subtree if archive_members else [folder_id]
    hub_ids = _pa.group_hub_ids_in(scope, root)
    archived: list[str] = []
    if hub_ids:
        from frontend.ui_web.agent_turns.state import is_agent_running

        for hub_id in hub_ids:
            targets = [hub_id]
            if archive_members:
                targets.extend(_pa.conversation_descendant_ids(hub_id, root))
            archived.extend(targets)
            for target_id in targets:
                if is_agent_running(target_id):
                    _pa.cancel_agent(target_id)
    # Marks every folder it removes deleted, so a stale folder list can't bring them back.
    with _FOLDER_WRITE_LOCK:
        removed = _pa.delete_folder(folder_id, root, archive_members=bool(archive_members))
    # Every open window drops the group (and what went with it) right away.
    _pa.notify_chats_changed(
        open_tab=False,
        removed_conv_ids=[*removed, *archived],
        removed_folder_ids=scope or [folder_id],
    )
    return removed


def rename_conversation(self, conv_id: str, title: str) -> None:
    conv = _pa.load_conversation(conv_id)
    if conv:
        conv.title = title.strip() or conv.title
        _pa.save_conversation(conv)
        # Other windows, @ mentions and search show the new name at once.
        _pa.notify_chats_changed(conv.id, conv.title, conv.folder_id, open_tab=False)


def move_chats_to_project(
    self,
    conv_ids: list[str],
    folder_ids: list[str],
    target_slug: str,
    folder_id: str = "",
) -> None:
    from frontend.ui_web.chats.folders import move_chats_to_project

    move_chats_to_project(
        [str(cid) for cid in (conv_ids or [])],
        [str(fid) for fid in (folder_ids or [])],
        str(target_slug or ""),
        str(folder_id or ""),
    )
    _pa.notify_chats_changed(open_tab=False)


def _chat_root(self, conv_id: str) -> str | None:
    """The island a chat lives on (None: the open one): Global Agents and other
    islands' chats are in the sidebar too."""
    from frontend.ui_web.chats.conversations import conversation_project_slug

    slug = conversation_project_slug(conv_id)
    try:
        return self._layout_project_root(slug) if slug else None
    except ValueError:
        return None


def move_conversation(self, conv_id: str, folder_id: str) -> None:
    archiving = _pa.is_archive_folder_id(folder_id)
    root = self._chat_root(conv_id)
    leaving: list[str] = []
    if archiving:
        from frontend.ui_web.agent_turns.state import is_agent_running

        leaving = [conv_id, *_pa.conversation_descendant_ids(conv_id, root)]
        conv = _pa.load_conversation(conv_id, root)
        if conv is not None and getattr(conv, "is_group", False):
            # A group goes to Archive with its duckies and nested groups.
            own = next((f for f in _pa.load_folders(root) if (f.group_hub_id or "") == conv_id), None)
            if own is not None:
                subtree = _pa.folder_subtree_ids(own.id, root)
                for hub_id in _pa.group_hub_ids_in(subtree, root):
                    leaving.extend([hub_id, *_pa.conversation_descendant_ids(hub_id, root)])
        leaving = list(dict.fromkeys(leaving))
        for target_id in leaving:
            if is_agent_running(target_id):
                _pa.cancel_agent(target_id)
    _pa.move_conversation(conv_id, folder_id or "", root)
    # Archived chats leave every open window's tree at once; a restore reloads them.
    _pa.notify_chats_changed(open_tab=False, removed_conv_ids=leaving)


def delete_conversation(self, conv_id: str, keep_inside: bool = False) -> None:
    from frontend.ui_web.agent_turns.state import is_agent_running

    root = self._chat_root(conv_id)
    if keep_inside:
        # Only the group goes: what was in it stays where it is, on its own.
        for child_id in _pa.conversation_descendant_ids(conv_id, root):
            child = _pa.load_conversation(child_id, root)
            if child is not None and (child.parent_conv_id or "").strip() == conv_id:
                child.parent_conv_id = ""
                child.leader_conv_id = ""
                _pa.save_conversation(child, root)
    # Its sub-agent chats (an archived group's duckies and nested groups too) are found
    # on its own island (Global Agents or another one).
    doomed = [conv_id, *_pa.conversation_descendant_ids(conv_id, root)]
    for target_id in doomed:
        if is_agent_running(target_id):
            _pa.cancel_agent(target_id)
    _pa.delete_conversation(conv_id, root)
    _pa.notify_chats_changed(open_tab=False, removed_conv_ids=doomed)


def _layout_project_root(slug: str) -> str | None:
    """Where a layout for this project is saved. None: the open island."""
    from frontend.ui_web.chats.store import project_root_for_slug, project_slug

    wanted = (slug or "").strip()
    if not wanted:
        return None
    current = getattr(_pa.PanelSettings.load(), "uefn_project_root", "") or ""
    if project_slug(current) == wanted:
        return None
    if wanted == GLOBAL_SLUG:
        return ""
    root = project_root_for_slug(wanted)
    if root is None:
        raise ValueError(f"Unknown project: {wanted}")
    return root


def apply_sidebar_layout(self, payload: dict[str, Any]) -> None:
    folders = payload.get("folders")
    chats = payload.get("chats")
    if not isinstance(folders, list) or not isinstance(chats, list):
        raise ValueError("layout payload must include folders and chats arrays")
    project_root = self._layout_project_root(str(payload.get("project_slug") or ""))
    # Unknown ids (deleted elsewhere) are skipped, never re-created.
    _pa.apply_sidebar_layout(folders=folders, chats=chats, project_root=project_root)
    # Drag in/out of a group folder is a roster change — refresh IN THIS CHAT
    # after disk is saved (the optimistic tree is too early to fetch against).
    _pa.notify_chats_changed(open_tab=False)
