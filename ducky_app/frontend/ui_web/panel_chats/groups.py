"""Groups (teams): create or reuse a group, seat and invite duckies, the roster and its
leader, members' models and write lanes, member observations and Stop team."""

from __future__ import annotations

import uuid
from typing import Any

import frontend.ui_web.panel_api as _pa
from frontend.ui_web.panel_chats.folders import _FOLDER_WRITE_LOCK


def _copy_name(profile: dict[str, Any], members: list[dict[str, Any]]) -> str:
    """The next free copy name for a profile already seated in a group: "Verse Coder 2"."""
    base = str(profile.get("name") or "").strip() or "Ducky"
    taken = {str(m.get(key) or "").strip() for m in members for key in ("name", "ducky_name")}
    n = 2
    while f"{base} {n}" in taken:
        n += 1
    return f"{base} {n}"


def _leader_agent_ok(err: Any, leader: Any) -> Any:
    """A seat refused on the coding agent its group's leader is running right now: that agent
    is available (the leader's turn proves it), whatever this process's detect cache says.
    Returns a ResolveOk for the seat's own pick, or None to keep the refusal."""
    from frontend.favorite_models import ResolveOk, parse_selection

    sel = parse_selection(getattr(err, "requested", "") or "")
    agent = str(getattr(leader, "coding_agent", "") or "").strip().lower().replace("-", "_")
    if sel is None or not sel.is_coding_agent or sel.backend != agent:
        return None
    # The agent's own refusal ("unavailable") or the very model the leader runs.
    if not str(err.message).startswith("Coding agent") and sel.model_id != str(getattr(leader, "model", "") or "").strip():
        return None
    return ResolveOk(coding_agent=sel.coding_agent, model=sel.model_id, provider="", selection=sel)


def group_create(
    self,
    name: str = "",
    folder_id: str = "",
    open_tab: bool = True,
    project_slug: str = "",
) -> dict[str, Any]:
    """Create a group as a folder: folder click opens the group hub chat.

    ``project_slug`` makes it on another island, or in Global Agents
    (``_no_project``); empty keeps the open island.
    """
    title = (name or "").strip() or "Group"
    settings = _pa.PanelSettings.load()
    project_root: str | None = None
    target_slug = (project_slug or "").strip()
    if target_slug:
        from frontend.ui_web.chats.store import create_project_root

        project_root = create_project_root(target_slug, settings.uefn_project_root or "")
    # Parent folder_id here means "create the group-folder inside this folder".
    parent_folder = (folder_id or "").strip()
    with _FOLDER_WRITE_LOCK:
        return self._group_create_locked(title, parent_folder, settings, project_root, open_tab)


def _group_create_locked(
    self, title: str, parent_folder: str, settings: Any, project_root: str | None, open_tab: bool
) -> dict[str, Any]:
    # The folder is saved already linked to its hub (Oct 25 2026): saved plain first and
    # linked two saves later, a sidebar refresh in between showed a stray plain folder
    # next to the group when groups were made fast.
    hub_id = str(uuid.uuid4())
    hub_folder = _pa.create_folder(title, parent_folder, project_root, group_hub_id=hub_id)
    conv = _pa.create_conversation(
        settings,
        hub_folder.id,
        title=title,
        ducky_style=_pa.default_bundled_style(),
        ducky_name="Group",
        project_root=project_root,
        conv_id=hub_id,
    )
    conv.is_group = True
    conv.leader_conv_id = ""
    conv.group_members = []
    # A group made inside another group's folder sits in that group, the way archiving
    # keeps it: deleting, archiving and team plans follow parent_conv_id. Oct 15 2026: a
    # lead's nested groups had none, so deleting its team left them and their members.
    outer = next((f for f in _pa.load_folders(project_root) if parent_folder and f.id == parent_folder), None)
    outer_hub = (getattr(outer, "group_hub_id", None) or "").strip() if outer is not None else ""
    if outer_hub:
        conv.parent_conv_id = outer_hub
    # Written before answering: is_group is not a placement field, so a plain save only
    # queued it, and a seat call right after found "not a group hub" (Nov 5 2026).
    _pa.save_conversation(conv, project_root, wait=True)
    stored = _pa.load_conversation(conv.id, project_root=project_root)
    if stored is None or not getattr(stored, "is_group", False):
        _pa.save_conversation(conv, project_root, wait=True, full=True)
        stored = _pa.load_conversation(conv.id, project_root=project_root)
    if stored is None or not getattr(stored, "is_group", False):
        from frontend.ui_web.chats.folders import save_folders

        save_folders([f for f in _pa.load_folders(project_root) if f.id != hub_folder.id], project_root)
        return {"ok": False, "error": f"Group {title!r} was not saved; nothing was created."}
    _pa.notify_chats_changed(conv.id, conv.title, conv.folder_id, open_tab=bool(open_tab))
    return {
        "ok": True,
        "id": conv.id,
        "title": conv.title,
        "is_group": True,
        "leader_conv_id": "",
        "group_members": [],
        "folder_id": hub_folder.id,
    }


def group_find_or_create(
    self, name: str = "", folder_id: str = "", open_tab: bool = False, caller_conv_id: str = ""
) -> dict[str, Any]:
    """Reuse a live group of this name anywhere on the caller's island, or create one there.

    The island comes from ``folder_id`` or ``caller_conv_id`` (the open island when
    neither is known). A same-name group inside ``folder_id`` wins over one elsewhere.
    Archived groups are skipped. The sidebar's own new-group button stays on
    ``group_create``, so a person can still make a duplicate on purpose.
    """
    from frontend.archive_folder import is_archive_folder_id
    from frontend.ui_web.chats.conversations import conversation_project_slug
    from frontend.ui_web.chats.folders import folder_project_slug

    title = (name or "").strip() or "Group"
    parent = (folder_id or "").strip()
    # Oct 22 2026: matching only inside the caller's folder made "Deploy core to Fly" 3 times
    # once the caller's chat had moved.
    slug = folder_project_slug(parent) or conversation_project_slug((caller_conv_id or "").strip())
    project_root: str | None = None
    if slug:
        from frontend.ui_web.chats.store import create_project_root

        project_root = create_project_root(slug, _pa.PanelSettings.load().uefn_project_root or "")
    candidates = [
        f for f in _pa.load_folders(project_root) if (getattr(f, "name", "") or "").strip() == title
    ]
    candidates.sort(key=lambda f: (getattr(f, "parent_id", "") or "").strip() != parent)
    for folder in candidates:
        hub_id = (getattr(folder, "group_hub_id", None) or "").strip()
        outer = (getattr(folder, "parent_id", "") or "").strip()
        if not hub_id or is_archive_folder_id(folder.id) or is_archive_folder_id(outer):
            continue
        hub = _pa.load_conversation(hub_id, project_root=project_root)
        if hub is None or not getattr(hub, "is_group", False):
            continue
        if is_archive_folder_id(getattr(hub, "folder_id", "") or ""):
            continue
        return {
            "ok": True,
            "id": hub.id,
            "title": hub.title,
            "is_group": True,
            "leader_conv_id": (getattr(hub, "leader_conv_id", None) or "").strip(),
            "group_members": getattr(hub, "group_members", None) or [],
            "folder_id": folder.id,
            "reused": True,
        }
    created = self.group_create(name=title, folder_id=parent, open_tab=open_tab, project_slug=slug)
    if created.get("ok"):
        created["reused"] = False
    return created


def group_seat_profile(
    self,
    group_id: str,
    profile_id: str,
    model: str = "",
    write_allowed: list[str] | None = None,
    for_agent: bool = False,
    exclude_conv_id: str = "",
    name: str = "",
) -> dict[str, Any]:
    """Seat a ducky in a group, reusing its idle chat instead of a new group.

    ``name`` names the seat by its role ("Search lead"): an idle seat of that name is
    reused, otherwise a new seat gets it. Without one, a busy copy (another node still
    running) gets the next name, "Verse Coder 2", in the same group. ``for_agent``: an agent is seating it, so it never lands on a
    pay-per-token API key the person has not opened to agents.
    """
    from frontend.agent_profiles import get_agent_profile
    from frontend.ui_web.agent_turns.state import is_agent_running, wait_for_idle
    from frontend.ui_web.group_orchestrator import group_members

    group = _pa.load_conversation(group_id)
    if not group or not getattr(group, "is_group", False):
        return {"ok": False, "error": "Not a group chat"}
    profile = get_agent_profile(profile_id)
    if not profile:
        return {"ok": False, "error": f"Unknown ducky profile: {profile_id}"}
    pid = str(profile.get("id") or profile_id).strip()
    members = group_members(group)
    same = [m for m in members if m.get("profile_id") == pid and not m.get("is_group")]
    wanted = (name or "").strip()
    if wanted:
        same = [m for m in same if str(m.get("name") or m.get("ducky_name") or "").strip() == wanted]
    for member in same:
        cid = str(member.get("member_conv_id") or "").strip()
        # The MCP bridge cannot see the caller's panel-process session.
        # Never reuse the requesting chat as its own worker.
        if not cid or cid == exclude_conv_id or _pa.load_conversation(cid) is None:
            continue
        # A finished turn's thread stays alive for a moment after the reply.
        # That is not a parallel branch; wait it out before making a copy.
        if is_agent_running(cid):
            wait_for_idle(cid, 3.0)
        if not is_agent_running(cid):
            if for_agent:
                blocked = self._agent_api_key_refusal(cid)
                if blocked:
                    return {"ok": False, "error": blocked}
            return {
                "ok": True,
                "member": member,
                "reused": True,
                "group_members": members,
                "leader_conv_id": (getattr(group, "leader_conv_id", None) or "").strip(),
            }
    copy = wanted or (_copy_name(profile, members) if same else "")
    seated = self.group_invite(
        group_id, pid, model=model, write_allowed=write_allowed, name=copy, for_agent=for_agent
    )
    if seated.get("ok"):
        seated["reused"] = False
    return seated


def _agent_api_key_refusal(conv_id: str) -> str:
    """Why an agent may not run work in this chat ("" = it may): the chat bills an API
    key per token that the person has not opened to agents."""
    from frontend.favorite_models import billing_kind

    conv = _pa.load_conversation(conv_id)
    if conv is None:
        return ""
    agent = str(getattr(conv, "coding_agent", "") or "ducky")
    provider = str(getattr(conv, "provider", "") or "")
    if billing_kind(agent, provider, str(getattr(conv, "model", "") or "")) != "api_key":
        return ""
    allowed = {str(p).strip().lower() for p in (_pa.PanelSettings.load().agent_api_keys or [])}
    if provider.strip().lower() in allowed:
        return ""
    return (f"Chat {conv.title!r} runs on the person's {provider} API key (billed per token); agents may "
            "not use it. Seat a ducky whose profile model is a subscription model (e.g. "
            "codex:…) instead, or ask the person to switch that chat's model or allow agents "
            "to use this key in Settings → LLMs.")


def group_invite(
    self,
    group_id: str,
    profile_id: str,
    model: str = "",
    write_allowed: list[str] | None = None,
    name: str = "",
    for_agent: bool = False,
) -> dict[str, Any]:
    """Spawn an independent member chat from a ducky profile and add it to the group.

    ``write_allowed`` seats the member with a write lane (see group_set_member_lane).
    """
    from frontend.agent_profiles import get_agent_profile
    from frontend.ducky_assets import ducky_style_label, normalize_ducky_style
    from frontend.favorite_models import ResolveErr, allow_create_without_model
    from frontend.ui_web.group_orchestrator import (
        _group_folder_id,
        group_members,
        member_color_for_index,
        normalize_member,
    )

    group = _pa.load_conversation(group_id)
    if not group or not getattr(group, "is_group", False):
        return {"ok": False, "error": "Not a group chat"}
    if write_allowed is not None:
        # Check the lane first: a refused lane used to leave the new member's chat behind.
        check = self.group_check_lane(group_id, "__new_member__", write_allowed)
        if not check.get("ok"):
            return {"ok": False, "error": "; ".join(check.get("errors") or ["lane refused"])}
    profile = get_agent_profile(profile_id)
    if not profile:
        return {"ok": False, "error": f"Unknown ducky profile: {profile_id}"}
    existing = group_members(group)
    pid = str(profile.get("id") or profile_id).strip()
    wanted = (name or "").strip()
    if wanted and any(wanted in (str(m.get("name") or "").strip(), str(m.get("ducky_name") or "").strip())
                      for m in existing):
        return {"ok": False, "error": f"A member of this group is already called {wanted!r}"}
    # A copy name ("Verse Coder 2") is how a profile gets a second seat. The UI invite
    # passes no name and still refuses a duplicate; an agent asking for a second one
    # gets the copy (Oct 21 2026: a lead asked for 2 Verse Coders and was refused).
    if not (name or "").strip() and any(m.get("profile_id") == pid for m in existing):
        if not for_agent:
            return {"ok": False, "error": "That ducky is already in this group"}
        name = _copy_name(profile, existing)
    settings = _pa.PanelSettings.load()
    override = (model or "").strip()
    favorites = [override] if override else profile.get("favorite_models")
    result = allow_create_without_model(
        _pa.resolve_model_selection(favorites, settings)
    )
    leader_id = (getattr(group, "leader_conv_id", None) or "").strip()
    if isinstance(result, ResolveErr) and leader_id:
        result = _leader_agent_ok(result, _pa.load_conversation(leader_id)) or result
    if for_agent:
        from frontend.favorite_models import refuse_paid_api_for_agent

        result = refuse_paid_api_for_agent(result, settings)
    if isinstance(result, ResolveErr):
        return {"ok": False, "error": result.message}
    disabled_packs = profile.get("disabled_packs")
    disabled_tools = profile.get("disabled_tool_ids")
    enabled_subs = profile.get("enabled_subskills")
    style = normalize_ducky_style(str(profile.get("ducky_style") or ""))
    # Library profile name (Verse Coder) — not avatar style label (Artist).
    ducky_name = (name or "").strip() or str(profile.get("name") or "").strip() or ducky_style_label(style)
    folder_id = _group_folder_id(group_id) or (group.folder_id or "").strip()
    if not folder_id:
        return {"ok": False, "error": "Group has no folder"}
    member = _pa.create_conversation(
        settings,
        folder_id,
        title=ducky_name,
        parent_conv_id=group_id,
        ducky_style=style,
        ducky_name=ducky_name,
        profile_id=pid,
        ducky_personality=str(profile.get("ducky_personality") or ""),
        tts_voice=str(profile.get("tts_voice") or "").strip(),
        tts_speed=float(profile.get("tts_speed") or 0.0),
        disabled_packs=disabled_packs if isinstance(disabled_packs, list) else None,
        enabled_subskills=enabled_subs if isinstance(enabled_subs, dict) else None,
        disabled_tool_ids=disabled_tools if isinstance(disabled_tools, list) else None,
        model=result.model,
        provider=result.provider or None,
        coding_agent=result.coding_agent,
    )
    _pa.adopt_group_project(member, group_id)
    row = normalize_member(
        {
            "member_conv_id": member.id,
            "profile_id": pid,
            "name": ducky_name,
            "ducky_name": ducky_name,
            "ducky_style": style,
            "model": result.selection.qualified if result.model else "",
            "coding_agent": result.coding_agent,
            "tts_voice": str(getattr(member, "tts_voice", None) or profile.get("tts_voice") or ""),
            "tts_speed": float(getattr(member, "tts_speed", None) or profile.get("tts_speed") or 0.0),
            "color": member_color_for_index(len(existing)),
        },
        index=len(existing),
    )
    # First invited member becomes leader when none set yet.
    if not (getattr(group, "leader_conv_id", None) or "").strip():
        group.leader_conv_id = member.id
    self._save_roster(group, [*existing, row])
    if write_allowed is not None:
        laned = self.group_set_member_lane(group_id, member.id, write_allowed)
        if not laned.get("ok"):
            return {"ok": False, "error": laned.get("error"), "member": row}
        group = _pa.load_conversation(group_id) or group
        row = next((m for m in group_members(group) if m.get("member_conv_id") == member.id), row)
    # Reload sidebar only — do not notify the member id (that auto-opens a tab)
    # and do not re-select the group (that remounts the pane on a stale roster).
    _pa.notify_chats_changed(group.id, group.title, group.folder_id, open_tab=False)
    return {
        "ok": True,
        "member": row,
        "group_members": group.group_members,
        "leader_conv_id": (getattr(group, "leader_conv_id", None) or "").strip(),
    }


def post_team_message(self, sender_id: str, receiver_id: str, body: str = "") -> dict[str, Any]:
    """Show a message one team member sent another in their group chat (from the tool server)."""
    from frontend.ui_web.agent_turns.push import get_panel_push
    from frontend.ui_web.group_orchestrator import post_team_message

    posted = post_team_message(str(sender_id or ""), str(receiver_id or ""), str(body or ""), push=get_panel_push())
    return {"ok": True, "posted": posted}


def group_set_leader(self, group_id: str, member_conv_id: str) -> dict[str, Any]:
    """Designate the spokesperson for a group (cross-group / nested routing)."""
    from frontend.ui_web.group_orchestrator import group_members

    group = _pa.load_conversation(group_id)
    if not group or not getattr(group, "is_group", False):
        return {"ok": False, "error": "Not a group chat"}
    mid = (member_conv_id or "").strip()
    if not mid:
        return {"ok": False, "error": "member_conv_id required"}
    members = group_members(group)
    if not any(m.get("member_conv_id") == mid for m in members):
        return {"ok": False, "error": "Member not in this group"}
    group.leader_conv_id = mid
    _pa.save_conversation(group)
    _sync_moves([mid], keep_hub=group.id)
    _pa.notify_chats_changed(group.id, group.title, group.folder_id, open_tab=False)
    return {"ok": True, "leader_conv_id": mid, "group_members": members}


def _sync_moves(conv_ids: list[str], keep_hub: str = "", root: str | None = None) -> dict[str, Any]:
    """Teams follow a re-nest: the moved chats' rows, leaders and parents (see sync_membership).
    A failed sync never fails the move itself."""
    from frontend.ui_web.chats.groups import sync_membership

    try:
        return sync_membership(root, conv_ids, keep_hub=keep_hub)
    except Exception:  # noqa: BLE001 - the move is saved; the next re-nest syncs again
        return {}


def group_add_member(
    self, group_id: str, conv_id: str, as_leader: bool = False, move_folder: bool = True
) -> dict[str, Any]:
    """Move an existing chat into a group folder and roster (optional as_leader).

    ``move_folder`` (default) files an as_leader chat in the group's folder too, so a
    workstream lead sits in the group it leads. A chat that leads the group it is filed
    in (the root lead, or a lead joining a sibling team) keeps its place.
    """
    from frontend.ui_web.group_orchestrator import (
        group_members,
        member_color_for_index,
        normalize_member,
        stays_with_outer_group,
        sync_group_members_from_folder,
    )
    from frontend.ui_web.group_orchestrator import _group_folder_id
    from frontend.ui_web.chats.groups import adopt_group_project, island_for_conversation

    group = _pa.load_conversation(group_id)
    if not group or not getattr(group, "is_group", False):
        return {"ok": False, "error": "Not a group chat"}
    cid = (conv_id or "").strip()
    member = _pa.load_conversation(cid)
    if member is None:
        return {"ok": False, "error": "Conversation not found"}
    if getattr(member, "is_group", False):
        return {"ok": False, "error": "Cannot add a group hub as a leaf member"}
    folder_id = _group_folder_id(group_id) or (group.folder_id or "").strip()
    if not folder_id:
        return {"ok": False, "error": "Group has no folder"}
    _convs, folders = island_for_conversation(group_id)
    # A member moved from another group keeps its lane: the new group's roster row is
    # built fresh from the folder and would otherwise drop it ("No lane").
    old_row = self._lane_row_in_other_group(member, group_id)
    # The lead of an outer group joins its subgroups' rosters but stays filed on top of
    # its own group; a chat leading the group it sits in is never pulled out of it
    # (Oct 22 2026: that left the old group empty). Everyone else moves in.
    move = not as_leader or move_folder
    if move and as_leader and self._leads_home_group(member, folders):
        move = False
    if move and not stays_with_outer_group(member, folder_id, folders):
        _pa.move_conversation(cid, folder_id)
        member = _pa.load_conversation(cid) or member
        member.parent_conv_id = group_id
        member.folder_id = folder_id
        _pa.save_conversation(member)
    adopt_group_project(member, group_id)
    sync_group_members_from_folder(group)
    group = _pa.load_conversation(group_id) or group
    existing = group_members(group)
    if not any(m.get("member_conv_id") == cid for m in existing):
        name = (
            str(getattr(member, "ducky_name", None) or "").strip()
            or str(member.title or "").strip()
            or "Ducky"
        )
        row = normalize_member(
            {
                "member_conv_id": cid,
                "profile_id": str(getattr(member, "profile_id", None) or ""),
                "name": name,
                "ducky_name": str(getattr(member, "ducky_name", None) or ""),
                "ducky_style": str(getattr(member, "ducky_style", None) or ""),
                "model": str(getattr(member, "model", None) or ""),
                "coding_agent": str(getattr(member, "coding_agent", None) or ""),
                "tts_voice": str(getattr(member, "tts_voice", None) or ""),
                "tts_speed": float(getattr(member, "tts_speed", None) or 0.0),
                "color": member_color_for_index(len(existing)),
            },
            index=len(existing),
        )
        existing = [*existing, row]
    if as_leader or not (getattr(group, "leader_conv_id", None) or "").strip():
        group.leader_conv_id = cid
    self._save_roster(group, existing)
    _sync_moves([cid], keep_hub=group_id)
    group = _pa.load_conversation(group_id) or group
    _pa.notify_chats_changed(group.id, group.title, group.folder_id, open_tab=False)
    out: dict[str, Any] = {
        "ok": True,
        "leader_conv_id": (getattr(group, "leader_conv_id", None) or "").strip(),
        "group_members": group_members(group),
        "folder_id": (getattr(member, "folder_id", None) or "").strip(),
    }
    now_row = next((m for m in out["group_members"] if m.get("member_conv_id") == cid), {})
    if old_row and now_row.get("write_allowed") is None:
        kept = self.group_set_member_lane(
            group_id, cid, old_row["write_allowed"], set_by=str(old_row.get("lane_set_by") or "user")
        )
        if kept.get("ok"):
            out["group_members"] = kept["group_members"]
        else:
            out["lane_warning"] = f"Lane not kept: {kept.get('error')}"
    return out


def _leads_home_group(member: Any, folders: list[Any]) -> bool:
    """True when ``member`` is the leader of the group whose folder it sits in."""
    home = (getattr(member, "folder_id", None) or "").strip()
    hub_id = next((str(getattr(f, "group_hub_id", "") or "").strip() for f in folders if f.id == home), "")
    hub = _pa.load_conversation(hub_id) if hub_id else None
    return hub is not None and (getattr(hub, "leader_conv_id", None) or "").strip() == member.id


def _lane_row_in_other_group(member: Any, group_id: str) -> dict[str, Any] | None:
    """The member's roster row in the group it is leaving, when that row has a lane."""
    from frontend.ui_web.group_orchestrator import group_members

    old_id = (getattr(member, "parent_conv_id", None) or "").strip()
    if not old_id or old_id == group_id:
        return None
    old = _pa.load_conversation(old_id)
    if not old or not getattr(old, "is_group", False):
        return None
    row = next((m for m in group_members(old) if m.get("member_conv_id") == member.id), None)
    return row if row and row.get("write_allowed") is not None else None


def group_set_member_model(
    self, group_id: str, member_conv_id: str, model: str = ""
) -> dict[str, Any]:
    """Set the model a group member uses for their turns."""
    from frontend.favorite_models import ResolveErr
    from frontend.ui_web.group_orchestrator import group_members, normalize_member

    group = _pa.load_conversation(group_id)
    if not group or not getattr(group, "is_group", False):
        return {"ok": False, "error": "Not a group chat"}
    mid = (member_conv_id or "").strip()
    members = group_members(group)
    if not any(m.get("member_conv_id") == mid for m in members):
        return {"ok": False, "error": "Member not in this group"}
    settings = _pa.PanelSettings.load()
    override = (model or "").strip()
    result = _pa.resolve_model_selection([override] if override else None, settings)
    if isinstance(result, ResolveErr):
        return {"ok": False, "error": result.message}
    agent_res = self.set_conversation_coding_agent(
        mid,
        result.coding_agent,
        result.model,
        result.provider or "",
    )
    if not agent_res.get("ok"):
        return {"ok": False, "error": agent_res.get("error") or "Failed to set model"}
    next_members = []
    for i, m in enumerate(members):
        row = dict(m)
        if row.get("member_conv_id") == mid:
            row["model"] = result.selection.qualified
            row["coding_agent"] = result.coding_agent
        next_members.append(normalize_member(row, index=i))
    next_members = self._save_roster(group, next_members)
    _pa.notify_chats_changed(group.id, group.title, group.folder_id, open_tab=False)
    return {"ok": True, "group_members": next_members}


def _save_roster(group: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every roster save: never puts back a teammate's older lane (Oct 24 2026)."""
    from frontend.ui_web.group_orchestrator import save_group_roster

    return save_group_roster(group, rows, save=_pa.save_conversation)

# -- write lanes ------------------------------------------------------------


def group_set_member_lane(
    self,
    group_id: str,
    member_conv_id: str,
    write_allowed: list[str] | str | None,
    force: bool = False,
    set_by: str = "user",
) -> dict[str, Any]:
    """Set (or clear with None) a member's write lane; refuses overlaps with teammates."""
    import time

    from backend.workspace import events, lanes as lane_engine
    from frontend.ui_web.group_orchestrator import group_members

    from frontend.ui_web.chats.store import _conversation_lock

    # Use the same reentrant group lock as save_conversation so competing
    # lane updates cannot both validate an obsolete roster snapshot.
    with _conversation_lock(group_id):
        group = _pa.load_conversation(group_id)
        if not group or not getattr(group, "is_group", False):
            return {"ok": False, "error": "Not a group chat"}
        mid = (member_conv_id or "").strip()
        try:
            rows, warnings = lane_engine.set_member_lane(
                group_members(group), mid, write_allowed, set_by=(set_by or "user"), now=time.time(), force=bool(force)
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        with lane_engine.setting_lane(mid):
            next_members = self._save_roster(group, rows)
        lane_engine.invalidate_lane_cache(mid)
    _pa.notify_chats_changed(group.id, group.title, group.folder_id, open_tab=False)
    lane = next((m.get("write_allowed") for m in next_members if m.get("member_conv_id") == mid), None)
    events.emit(events.LaneChangedEvent(group.id, mid, None if lane is None else tuple(lane)).to_dict())
    return {
        "ok": True,
        "group_members": next_members,
        "warnings": warnings,
        "lanes": lane_engine.lane_set_view(group.id, next_members),
    }


def group_lane_authorities(self, group_id: str) -> list[str]:
    """Who may set lanes in this group: its leader, then the leader of every group it
    is nested in (an outer lead seats its subgroups' workers)."""
    from frontend.ui_web.group_orchestrator import _group_folder_id
    from frontend.ui_web.chats.groups import island_for_conversation

    gid = (group_id or "").strip()
    group = _pa.load_conversation(gid)
    if not group or not getattr(group, "is_group", False):
        return []
    leaders = [(getattr(group, "leader_conv_id", None) or "").strip()]
    _convs, folders = island_for_conversation(gid)
    rows = {f.id: f for f in folders}
    seen: set[str] = set()
    cur = (getattr(rows.get(_group_folder_id(gid)), "parent_id", None) or "").strip()
    while cur and cur not in seen:
        seen.add(cur)
        hub_id = (getattr(rows.get(cur), "group_hub_id", None) or "").strip()
        hub = _pa.load_conversation(hub_id) if hub_id else None
        if hub is not None and getattr(hub, "is_group", False):
            leaders.append((getattr(hub, "leader_conv_id", None) or "").strip())
        cur = (getattr(rows.get(cur), "parent_id", None) or "").strip()
    return [lid for lid in leaders if lid]


def group_get_lanes(self, group_id: str) -> dict[str, Any]:
    from backend.workspace import lanes as lane_engine
    from frontend.ui_web.group_orchestrator import group_members

    group = _pa.load_conversation(group_id)
    if not group or not getattr(group, "is_group", False):
        return {"ok": False, "error": "Not a group chat"}
    return {"ok": True, **lane_engine.lane_set_view(group.id, group_members(group))}


def group_check_lane(
    self, group_id: str, member_conv_id: str, write_allowed: list[str] | str | None
) -> dict[str, Any]:
    """Dry run for the lane editor: normalized globs plus overlap errors/warnings."""
    from backend.workspace import lanes as lane_engine
    from frontend.ui_web.group_orchestrator import group_members

    group = _pa.load_conversation(group_id)
    if not group or not getattr(group, "is_group", False):
        return {"ok": False, "errors": ["Not a group chat"], "warnings": [], "normalized": None}
    try:
        lane = lane_engine.normalize_lane(write_allowed)
    except lane_engine.LaneGlobError as exc:
        return {"ok": False, "errors": [str(exc)], "warnings": [], "normalized": None}
    proposed = {str(m.get("member_conv_id") or ""): m.get("write_allowed") for m in group_members(group)}
    proposed[(member_conv_id or "").strip()] = lane
    verdict = lane_engine.check_lane_set(proposed)
    return {"ok": not verdict["errors"], "normalized": lane, **verdict}


def group_remove(self, group_id: str, member_conv_id: str) -> dict[str, Any]:
    from frontend.ui_web.group_orchestrator import evict_member_from_group, group_members

    group = _pa.load_conversation(group_id)
    if not group or not getattr(group, "is_group", False):
        return {"ok": False, "error": "Not a group chat"}
    mid = (member_conv_id or "").strip()
    kept = [m for m in group_members(group) if m.get("member_conv_id") != mid]
    leader = (getattr(group, "leader_conv_id", None) or "").strip()
    if leader and leader == mid:
        group.leader_conv_id = str(kept[0].get("member_conv_id") or "") if kept else ""
    # Persist leader/roster first so a later folder-sync cannot pull this member back.
    self._save_roster(group, kept)
    evict_member_from_group(mid)
    _pa.notify_chats_changed(group.id, group.title, group.folder_id, open_tab=False)
    return {
        "ok": True,
        "group_members": kept,
        "leader_conv_id": (getattr(group, "leader_conv_id", None) or "").strip(),
    }


def group_members(self, group_id: str) -> dict[str, Any]:
    from frontend.ui_web.group_orchestrator import (
        _rehome_group_roster,
        group_members,
        is_group_conversation,
        sync_group_members_from_folder,
    )

    group = _pa.load_conversation(group_id)
    if not group:
        return {"ok": False, "error": "Conversation not found", "members": []}
    if is_group_conversation(group):
        moved = _rehome_group_roster(group)
        sync_group_members_from_folder(group)
        group = _pa.load_conversation(group_id) or group
        if moved:
            _pa.notify_chats_changed(group.id, group.title, group.folder_id, open_tab=False)
    # Read observations only; never persist runtime or duplicate plan state.
    import time
    from frontend.ui_web.chats.conversations import conversation_project_slug
    from frontend.ui_web.chats.store import project_root_for_slug
    from backend.agent.coding_agents.plans import load_plan_view
    from frontend.ui_web.agent_turns.push import get_panel_push

    members = group_members(group) if is_group_conversation(group) else []
    try:
        # A bridge process has no authoritative view of panel sessions.
        running = self.list_running_agents() if get_panel_push() is not None else None
        if not isinstance(running, list) or not all(isinstance(cid, str) for cid in running):
            running = None
    except Exception:
        running = None
    observed_at = time.time()
    enriched = []
    for member in members:
        row = dict(member)
        observation: dict[str, Any] = {
            "observed_at": observed_at, "runtime": "unknown", "assignment": None,
            "group_id": group.id, "role": "unknown", "project_slug": None,
        }
        row["observation"] = observation
        enriched.append(row)
        try:
            cid = str(member.get("member_conv_id") or "")
            conv = _pa.load_conversation(cid)
            if conv is None or not self._filed_with_group(conv, group.id):
                continue
            slug = conversation_project_slug(cid)
            if not slug or slug != conversation_project_slug(group.id):
                continue
            observation["project_slug"] = slug
            observation["role"] = "leader" if cid == getattr(group, "leader_conv_id", "") else "member"
            if running is not None:
                observation["runtime"] = "running" if cid in running else "idle"
            root = "" if slug == "_no_project" else project_root_for_slug(slug)
            if root is None:
                continue  # Unknown stored project: never use the active island.
            # Plans are found up the chat's parent chain; a member filed in another
            # group (an outer lead) shows this group's assignment instead.
            filed_here = (getattr(conv, "parent_conv_id", None) or "").strip() == group.id
            plan = load_plan_view(cid if filed_here else group.id, project_root=root)
            if not plan and not filed_here:
                plan = load_plan_view(cid, project_root=root)
            if not plan:
                continue
            source = plan.get("assigned_from") or {}
            node_id = source.get("node_id")
            node = next((n for n in plan.get("nodes", []) if n.get("id") == node_id), None)
            if node_id and node is None:
                continue
            observation["assignment"] = {
                "plan_id": plan.get("id"), "chat_id": plan.get("chat_id"),
                "node_id": node_id, "title": (node or plan).get("content", plan.get("title", "")),
                "status": (node or plan).get("status", "unknown"),
            }
        except Exception:
            # Ambiguous/deleted assignments and unavailable stores stay unknown.
            pass
    return {
        "ok": True,
        "is_group": is_group_conversation(group),
        "leader_conv_id": (getattr(group, "leader_conv_id", None) or "").strip(),
        "members": enriched,
    }


def _filed_with_group(conv: Any, group_id: str) -> bool:
    """A roster member counts wherever it is filed: in this group, or in another live
    group (a lead that stays on top of its outer group). A parent that no longer exists
    is a stale row."""
    parent_id = (getattr(conv, "parent_conv_id", None) or "").strip()
    if parent_id == group_id:
        return True
    parent = _pa.load_conversation(parent_id) if parent_id else None
    return parent is not None and bool(getattr(parent, "is_group", False))


def group_stop(self, group_id: str) -> dict[str, Any]:
    """Stop every running member's turn in this group and every group nested in it, never
    the group's lead (Oct 24 2026: Stop team also stopped the lead). Nothing is deleted;
    members can be started again."""
    from frontend.ui_web.team_keeper import stop_team

    return stop_team(group_id, self.cancel_agent)
