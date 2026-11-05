"""Where an agent's spawns land: always inside the agent's own group, never outside it.

Nov 5 2026: a lead's new group landed at the top level, then the lead took that group's lead
from the member it spawned to lead it; nobody could tell whose team the new lead was on.

These helpers read the store through ``ducky_panel`` (looked up at call time), so tests that
patch ``ducky_panel.load_conversation`` / ``load_folders`` / ``_project_root`` cover them too.
"""

from __future__ import annotations

from typing import Any


def _dp():
    from backend.tools.panel import ducky_panel

    return ducky_panel


def _caller_own_group(caller: str) -> str:
    """The group hub this chat already belongs to, or ''."""
    if not (caller or "").strip():
        return ""
    from frontend.ui_web.group_orchestrator import is_group_conversation

    dp = _dp()
    root = dp._project_root()
    conv = dp.load_conversation(caller, project_root=root)
    if conv is None:
        return ""
    if is_group_conversation(conv):
        return caller
    parent_id = (getattr(conv, "parent_conv_id", None) or "").strip()
    if not parent_id:
        return ""
    parent = dp.load_conversation(parent_id, project_root=root)
    if parent is not None and is_group_conversation(parent):
        return parent_id
    return ""


def _resolve_group_hub_id(group_id: str = "", folder_id: str = "") -> str:
    """Resolve a group hub id from an explicit hub id or a group folder id."""
    dp = _dp()
    root = dp._project_root()
    gid = (group_id or "").strip()
    if gid:
        dp.flush_chat_saves(gid, timeout=10.0)  # a group made a moment ago may still be queued
        hub = dp.load_conversation(gid, project_root=root)
        if hub is not None and getattr(hub, "is_group", False):
            return gid
        # A member's chat id (what a spawn answered with) means the group it sits in.
        parent_id = str(getattr(hub, "parent_conv_id", "") or "").strip() if hub is not None else ""
        parent = dp.load_conversation(parent_id, project_root=root) if parent_id else None
        if parent is not None and getattr(parent, "is_group", False):
            return parent_id
        if hub is None and not (folder_id or "").strip():
            try:
                return _resolve_group_hub_id(folder_id=gid)  # a group's folder id
            except ValueError:
                pass
        raise ValueError(f"{gid!r} is not a group hub. Pass the group hub id from ducky_group_create.")
    fid = (folder_id or "").strip()
    if fid and fid != "default":
        for folder in dp.load_folders(root):
            if folder.id == fid:
                hub_id = (getattr(folder, "group_hub_id", None) or "").strip()
                if hub_id:
                    return hub_id
                break
        raise ValueError(
            f"Folder {fid!r} is not a group folder. Create one with ducky_group_create first."
        )
    return ""


def _lead_if_leaderless(hub_id: str, caller: str, root: str | None) -> None:
    """The chat that seats a member into a group with no leader leads it (a pointer: the chat is
    not moved). Nov 3 2026: a lead spawned into a group by id, and its later ducky_group_invite
    there was refused with "This group has no leader yet"."""
    if not caller or caller == hub_id:
        return
    dp = _dp()
    hub = dp.load_conversation(hub_id, project_root=root)
    if hub is not None and not (getattr(hub, "leader_conv_id", None) or "").strip():
        hub.leader_conv_id = caller
        dp.save_conversation(hub, root)


def _is_lead_seat(name: str, as_leader: bool) -> bool:
    """A seat that leads its group itself: as_leader=true, or named "Lead: …"."""
    return bool(as_leader) or (name or "").strip().lower().startswith("lead:")


def _seat_leads(hub_id: str, member_id: str, root: str | None, *, force: bool = False) -> None:
    """The new member leads the group, not the chat that seated it. ``force`` replaces a leader."""
    if not member_id:
        return
    dp = _dp()
    hub = dp.load_conversation(hub_id, project_root=root)
    if hub is None:
        return
    current = (getattr(hub, "leader_conv_id", None) or "").strip()
    if current == member_id or (current and not force):
        return
    hub.leader_conv_id = member_id
    dp.save_conversation(hub, root)


def _group_folder_of(hub_id: str, folders: list[Any]) -> str:
    """The folder a group hub owns, or ''."""
    return next((f.id for f in folders if (getattr(f, "group_hub_id", None) or "").strip() == hub_id), "")


def _folder_inside(folder_id: str, outer_id: str, folders: list[Any]) -> bool:
    """True when ``folder_id`` is ``outer_id`` or nested somewhere under it."""
    rows = {f.id: f for f in folders}
    cur, seen = folder_id, set()
    while cur and cur not in seen:
        if cur == outer_id:
            return True
        seen.add(cur)
        cur = (getattr(rows.get(cur), "parent_id", None) or "").strip()
    return False


def _caller_team(caller: str, folders: list[Any]) -> str:
    """The group this caller works in: the group it belongs to, else a group it leads."""
    own = _caller_own_group(caller)
    if own or not caller:
        return own
    dp = _dp()
    root = dp._project_root()
    for folder in folders:
        hub_id = (getattr(folder, "group_hub_id", None) or "").strip()
        hub = dp.load_conversation(hub_id, project_root=root) if hub_id else None
        if hub is not None and (getattr(hub, "leader_conv_id", None) or "").strip() == caller:
            return hub_id
    return ""


def _spawn_parent_folder(caller: str, parent_folder_id: str) -> str:
    """Where an agent's new group goes: its group's folder when none is named; a named folder
    outside the agent's group is refused. A caller in no group keeps its top-level groups."""
    if not caller:
        return parent_folder_id
    folders = list(_dp().load_folders(_dp()._project_root()))
    team = _caller_team(caller, folders)
    team_folder = _group_folder_of(team, folders) if team else ""
    if not team_folder:
        return parent_folder_id
    if not parent_folder_id:
        return team_folder
    if not _folder_inside(parent_folder_id, team_folder, folders):
        raise ValueError(f"Spawn inside your own group: {team_folder}")
    return parent_folder_id


def _hub_inside(hub_id: str, outer_hub: str, root: str | None) -> bool:
    """True when group ``hub_id`` is ``outer_hub`` or nested in it (parent chain, then folders)."""
    dp = _dp()
    cur, seen = hub_id, set()
    while cur and cur not in seen:
        if cur == outer_hub:
            return True
        seen.add(cur)
        conv = dp.load_conversation(cur, project_root=root)
        cur = (getattr(conv, "parent_conv_id", None) or "").strip() if conv is not None else ""
    folders = list(dp.load_folders(root))
    inner, outer = _group_folder_of(hub_id, folders), _group_folder_of(outer_hub, folders)
    return bool(inner and outer and _folder_inside(inner, outer, folders))


def _require_own_team(hub_id: str, caller: str, root: str | None) -> None:
    """An agent seats members only in its own group, a group nested in it, or a group it leads."""
    if not caller or caller == hub_id:
        return
    hub = _dp().load_conversation(hub_id, project_root=root)
    if hub is not None and (getattr(hub, "leader_conv_id", None) or "").strip() == caller:
        return
    team = _caller_team(caller, list(_dp().load_folders(root)))
    if team and not _hub_inside(hub_id, team, root):
        raise ValueError(f"Spawn inside your own group: {team}")
