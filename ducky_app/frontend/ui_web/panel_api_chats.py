"""Folders, conversations, groups, profiles, duckies, send/cancel. Mixin for PanelApi — methods stay on the PyWebView JS object."""

from __future__ import annotations

from typing import Any

import frontend.ui_web.panel_api as _pa


class PanelApiChatsMixin:
    def list_folders(self) -> list[dict[str, str | float]]:
        _pa.ensure_group_folder_hubs()
        return [
            {
                "id": f.id,
                "name": f.name,
                "parent_id": f.parent_id,
                "sort_order": f.sort_order,
                "group_hub_id": getattr(f, "group_hub_id", "") or "",
            }
            for f in _pa.load_folders()
        ]

    def list_conversations(self, folder_id: str) -> list[dict[str, str | float | int]]:
        return [
            self._conversation_sidebar_row(c)
            for c in _pa.list_conversations(folder_id)
        ]

    def list_all_conversations(self) -> list[dict[str, str | float | int | bool]]:
        """All project chats in one call (metadata only) for sidebar grouping."""
        convs = _pa.list_all_conversation_metadata()
        group_ids = {c.id for c in convs if getattr(c, "is_group", False)}
        return [self._conversation_sidebar_row(c, group_ids=group_ids) for c in convs]

    def list_conversations_for_file(self, file_path: str) -> list[dict[str, str | float | int]]:
        return [
            self._conversation_sidebar_row(c)
            for c in _pa.list_conversations_for_file(file_path)
        ]

    @staticmethod
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

    @staticmethod
    def _conversation_sidebar_row(
        c: Any,
        *,
        group_ids: set[str] | None = None,
    ) -> dict[str, str | float | int | bool]:
        del group_ids  # retained for call-site compat; subagents retired
        parent_id = (getattr(c, "parent_conv_id", None) or "").strip()
        return {
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
            # Subagents retired — group members nest under hubs, never parent-linked seats.
            "is_subagent": False,
            "leader_conv_id": (getattr(c, "leader_conv_id", None) or "").strip(),
            "group_members": list(getattr(c, "group_members", None) or []),
            "tool_call_count": int(getattr(c, "tool_call_count", 0) or 0),
            "file_count": int(getattr(c, "file_count", 0) or 0),
            "context_tokens": _pa.PanelApi._sidebar_context_tokens(c),
        }

    def create_folder(self, name: str, parent_id: str = "") -> dict[str, str]:
        f = _pa.create_folder(name, parent_id)
        return {"id": f.id, "name": f.name}

    def rename_folder(self, folder_id: str, name: str) -> None:
        _pa.rename_folder(folder_id, name)

    def delete_folder(self, folder_id: str) -> list[str]:
        """Delete a folder. Returns the group hub chat ids it took with it."""
        # Deleting a group takes its nested groups too, so stop every runner in
        # the subtree first (same as Archive).
        hub_ids = _pa.group_hub_ids_in(_pa.folder_subtree_ids(folder_id))
        if hub_ids:
            from frontend.ui_web.agent_modes import cancel_agent, is_agent_running

            for hub_id in hub_ids:
                for target_id in [hub_id, *_pa.conversation_descendant_ids(hub_id)]:
                    if is_agent_running(target_id):
                        _pa.cancel_agent(target_id)
        return _pa.delete_folder(folder_id)

    def create_conversation(
        self,
        folder_id: str,
        ducky_style: str | None = None,
        file_path: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        style = ducky_style if ducky_style is not None else _pa.default_bundled_style()
        settings = _pa.PanelSettings.load()
        cfg = config if isinstance(config, dict) else {}
        title = str(cfg.get("title") or "").strip()
        ducky_name = str(cfg.get("ducky_name") or "").strip()
        profile_id = str(cfg.get("profile_id") or "").strip()
        personality = str(cfg.get("ducky_personality") or "")
        tts_voice = str(cfg.get("tts_voice") or "").strip()
        tts_speed = float(cfg.get("tts_speed") or 0.0)
        thinking_effort = str(cfg.get("thinking_effort") or "").strip().lower()
        disabled_packs = cfg.get("disabled_packs")
        enabled_subskills = cfg.get("enabled_subskills")
        disabled_tool_ids = cfg.get("disabled_tool_ids")
        if isinstance(cfg.get("ducky_style"), str) and cfg.get("ducky_style").strip():
            style = str(cfg.get("ducky_style")).strip()

        from backend.agent.coding_agents.base import normalize_coding_agent
        from frontend.favorite_models import ResolveErr, ResolveOk

        coding_agent = "ducky"
        resolved_model = ""
        resolved_provider = ""

        if "favorite_models" in cfg:
            # Profile-driven create: the profile's model, else the global default.
            result = _pa.resolve_model_selection(cfg.get("favorite_models"), settings)
            if isinstance(result, ResolveErr):
                raise ValueError(result.message)
            coding_agent = result.coding_agent
            resolved_model = result.model
            resolved_provider = result.provider
        else:
            explicit_agent = str(cfg.get("coding_agent") or "").strip()
            coding_agent = normalize_coding_agent(explicit_agent or "ducky")
            # Branded chats (e.g. Duck-Tac-Toe) pass coding_agent="ducky" so a
            # Cursor/Codex Default Model must not hijack the conversation.
            force_ducky = bool(explicit_agent) and coding_agent == "ducky"
            if coding_agent == "ducky":
                # Blank chat — seed the global Default Model (Settings → LLMs) so
                # the composer opens with a working model. No default (or an
                # unavailable one) leaves the model empty for a manual pick.
                result = _pa.resolve_model_selection(None, settings)
                if isinstance(result, ResolveOk):
                    if force_ducky and result.coding_agent != "ducky":
                        api_pick = _pa._first_available_api_model()
                        if api_pick:
                            resolved_provider, resolved_model = api_pick
                        # else: leave model empty — keep coding_agent ducky
                    else:
                        coding_agent = result.coding_agent
                        resolved_model = result.model
                        resolved_provider = result.provider

        explicit_agent = str(cfg.get("coding_agent") or "").strip()
        if explicit_agent and "favorite_models" in cfg:
            # Explicit coding_agent in config can only override when the model already
            # resolved to that agent (or ducky). Never invent a model for a forced agent.
            forced = normalize_coding_agent(explicit_agent)
            if forced != "ducky" and forced != coding_agent and not resolved_model:
                raise ValueError(
                    f"Cannot force coding agent {forced!r} without an exact model selection."
                )
            if forced != "ducky":
                coding_agent = forced

        conv = _pa.create_conversation(
            settings,
            folder_id,
            title=title,
            ducky_style=style,
            ducky_name=ducky_name,
            profile_id=profile_id,
            ducky_personality=personality,
            tts_voice=tts_voice,
            tts_speed=tts_speed,
            thinking_effort=thinking_effort,
            file_path=file_path or "",
            disabled_packs=disabled_packs if isinstance(disabled_packs, list) else None,
            enabled_subskills=enabled_subskills if isinstance(enabled_subskills, dict) else None,
            disabled_tool_ids=disabled_tool_ids if isinstance(disabled_tool_ids, list) else None,
            model=resolved_model,
            provider=resolved_provider,
            coding_agent=coding_agent,
        )
        _pa.notify_chats_changed(conv.id, conv.title, folder_id)
        return {
            "id": conv.id,
            "title": conv.title,
            "ducky_style": conv.ducky_style,
            "ducky_name": conv.ducky_name or "",
            "profile_id": getattr(conv, "profile_id", None) or "",
            "ducky_personality": conv.ducky_personality,
            "tts_voice": getattr(conv, "tts_voice", None) or "",
            "tts_speed": float(getattr(conv, "tts_speed", 0) or 0.0),
            "thinking_effort": getattr(conv, "thinking_effort", None) or "",
            "file_path": conv.file_path,
            "model": conv.model or "",
            "provider": conv.provider or "",
            "coding_agent": getattr(conv, "coding_agent", None) or "ducky",
        }

    def apply_ducky_config(self, conv_id: str, config: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(config, dict):
            return {"ok": False, "error": "config must be an object"}
        disabled_packs = config.get("disabled_packs")
        enabled_subskills = config.get("enabled_subskills")
        disabled_tool_ids = config.get("disabled_tool_ids")
        title = config.get("title")
        ducky_name = config.get("ducky_name")
        profile_id = config.get("profile_id")
        _pa.apply_ducky_config(
            conv_id,
            ducky_style=str(config.get("ducky_style") or _pa.default_bundled_style()),
            ducky_name=str(ducky_name) if ducky_name is not None else None,
            profile_id=str(profile_id) if profile_id is not None else None,
            ducky_personality=str(config.get("ducky_personality") or ""),
            tts_voice=str(config["tts_voice"]) if "tts_voice" in config else None,
            tts_speed=float(config["tts_speed"]) if "tts_speed" in config else None,
            thinking_effort=str(config["thinking_effort"]) if "thinking_effort" in config else None,
            title=str(title) if title is not None else None,
            disabled_packs=disabled_packs if isinstance(disabled_packs, list) else None,
            enabled_subskills=enabled_subskills if isinstance(enabled_subskills, dict) else None,
            disabled_tool_ids=disabled_tool_ids if isinstance(disabled_tool_ids, list) else None,
        )
        return {"ok": True}

    def group_create(self, name: str = "", folder_id: str = "") -> dict[str, Any]:
        """Create a group as a folder: folder click opens the group hub chat."""
        title = (name or "").strip() or "Group"
        settings = _pa.PanelSettings.load()
        # Parent folder_id here means "create the group-folder inside this folder".
        parent_folder = (folder_id or "").strip()
        hub_folder = _pa.create_folder(title, parent_folder)
        conv = _pa.create_conversation(
            settings,
            hub_folder.id,
            title=title,
            ducky_style=_pa.default_bundled_style(),
            ducky_name="Group",
        )
        conv.is_group = True
        conv.leader_conv_id = ""
        conv.group_members = []
        _pa.save_conversation(conv)
        # Link folder → hub so the sidebar treats the folder as the group.
        folders = _pa.load_folders()
        for f in folders:
            if f.id == hub_folder.id:
                f.group_hub_id = conv.id
                break
        _pa.save_folders(folders)
        _pa.notify_chats_changed(conv.id, conv.title, conv.folder_id)
        return {
            "ok": True,
            "id": conv.id,
            "title": conv.title,
            "is_group": True,
            "leader_conv_id": "",
            "group_members": [],
            "folder_id": hub_folder.id,
        }

    def group_invite(self, group_id: str, profile_id: str, model: str = "") -> dict[str, Any]:
        """Spawn an independent member chat from a ducky profile and add it to the group."""
        from frontend.agent_profiles import get_agent_profile
        from frontend.ducky_assets import ducky_style_label, normalize_ducky_style
        from frontend.favorite_models import ResolveErr
        from frontend.ui_web.group_orchestrator import group_members, member_color_for_index, normalize_member

        group = _pa.load_conversation(group_id)
        if not group or not getattr(group, "is_group", False):
            return {"ok": False, "error": "Not a group chat"}
        profile = get_agent_profile(profile_id)
        if not profile:
            return {"ok": False, "error": f"Unknown ducky profile: {profile_id}"}
        existing = group_members(group)
        pid = str(profile.get("id") or profile_id).strip()
        if any(m.get("profile_id") == pid for m in existing):
            return {"ok": False, "error": "That ducky is already in this group"}
        settings = _pa.PanelSettings.load()
        override = (model or "").strip()
        favorites = [override] if override else profile.get("favorite_models")
        result = _pa.resolve_model_selection(favorites, settings)
        if isinstance(result, ResolveErr):
            return {"ok": False, "error": result.message}
        disabled_packs = profile.get("disabled_packs")
        disabled_tools = profile.get("disabled_tool_ids")
        enabled_subs = profile.get("enabled_subskills")
        style = normalize_ducky_style(str(profile.get("ducky_style") or ""))
        # Library profile name (Verse Coder) — not avatar style label (Artist).
        ducky_name = str(profile.get("name") or "").strip() or ducky_style_label(style)
        member = _pa.create_conversation(
            settings,
            group.folder_id or "",
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
        row = normalize_member(
            {
                "member_conv_id": member.id,
                "profile_id": pid,
                "name": ducky_name,
                "ducky_name": ducky_name,
                "ducky_style": style,
                "model": result.selection.qualified,
                "coding_agent": result.coding_agent,
                "tts_voice": str(getattr(member, "tts_voice", None) or profile.get("tts_voice") or ""),
                "tts_speed": float(getattr(member, "tts_speed", None) or profile.get("tts_speed") or 0.0),
                "color": member_color_for_index(len(existing)),
            },
            index=len(existing),
        )
        group.group_members = [*existing, row]
        # First invited member becomes leader when none set yet.
        if not (getattr(group, "leader_conv_id", None) or "").strip():
            group.leader_conv_id = member.id
        _pa.save_conversation(group)
        # Reload sidebar only — do not notify the member id (that auto-opens a tab).
        _pa.notify_chats_changed(group.id, group.title, group.folder_id)
        return {
            "ok": True,
            "member": row,
            "group_members": group.group_members,
            "leader_conv_id": (getattr(group, "leader_conv_id", None) or "").strip(),
        }

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
        _pa.notify_chats_changed(group.id, group.title, group.folder_id)
        return {"ok": True, "leader_conv_id": mid, "group_members": members}

    def group_add_member(
        self, group_id: str, conv_id: str, as_leader: bool = False
    ) -> dict[str, Any]:
        """Move an existing chat into a group folder and roster (optional as_leader)."""
        from frontend.ui_web.group_orchestrator import (
            group_members,
            member_color_for_index,
            normalize_member,
            sync_group_members_from_folder,
        )
        from frontend.ui_web.project_chats import move_conversation

        group = _pa.load_conversation(group_id)
        if not group or not getattr(group, "is_group", False):
            return {"ok": False, "error": "Not a group chat"}
        cid = (conv_id or "").strip()
        member = _pa.load_conversation(cid)
        if member is None:
            return {"ok": False, "error": "Conversation not found"}
        if getattr(member, "is_group", False):
            return {"ok": False, "error": "Cannot add a group hub as a leaf member"}
        folder_id = (group.folder_id or "").strip()
        if not folder_id:
            return {"ok": False, "error": "Group has no folder"}
        _pa.move_conversation(cid, folder_id)
        member = _pa.load_conversation(cid) or member
        member.parent_conv_id = group_id
        _pa.save_conversation(member)
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
            group.group_members = [*existing, row]
        if as_leader or not (getattr(group, "leader_conv_id", None) or "").strip():
            group.leader_conv_id = cid
        _pa.save_conversation(group)
        _pa.notify_chats_changed(group.id, group.title, group.folder_id)
        return {
            "ok": True,
            "leader_conv_id": (getattr(group, "leader_conv_id", None) or "").strip(),
            "group_members": group_members(group),
        }

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
        group.group_members = next_members
        _pa.save_conversation(group)
        _pa.notify_chats_changed(group.id, group.title, group.folder_id)
        return {"ok": True, "group_members": next_members}

    def group_remove(self, group_id: str, member_conv_id: str) -> dict[str, Any]:
        from frontend.ui_web.group_orchestrator import group_members

        group = _pa.load_conversation(group_id)
        if not group or not getattr(group, "is_group", False):
            return {"ok": False, "error": "Not a group chat"}
        mid = (member_conv_id or "").strip()
        kept = [m for m in group_members(group) if m.get("member_conv_id") != mid]
        group.group_members = kept
        leader = (getattr(group, "leader_conv_id", None) or "").strip()
        if leader and leader == mid:
            group.leader_conv_id = str(kept[0].get("member_conv_id") or "") if kept else ""
        _pa.save_conversation(group)
        _pa.notify_chats_changed(group.id, group.title, group.folder_id)
        return {
            "ok": True,
            "group_members": kept,
            "leader_conv_id": (getattr(group, "leader_conv_id", None) or "").strip(),
        }

    def group_members(self, group_id: str) -> dict[str, Any]:
        from frontend.ui_web.group_orchestrator import (
            group_members,
            is_group_conversation,
            sync_group_members_from_folder,
        )

        group = _pa.load_conversation(group_id)
        if not group:
            return {"ok": False, "error": "Conversation not found", "members": []}
        if is_group_conversation(group):
            sync_group_members_from_folder(group)
            group = _pa.load_conversation(group_id) or group
        return {
            "ok": True,
            "is_group": is_group_conversation(group),
            "leader_conv_id": (getattr(group, "leader_conv_id", None) or "").strip(),
            "members": group_members(group) if is_group_conversation(group) else [],
        }

    def list_agent_profiles(self) -> dict[str, Any]:
        profiles = _pa.list_agent_profiles()
        return {
            "profiles": profiles,
            "template_profiles": _pa.list_bundled_agent_profile_templates(),
            "blank_profile_id": _pa.BLANK_PROFILE_ID,
        }

    def save_agent_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(profile, dict):
            raise ValueError("profile must be an object")
        saved = _pa.save_agent_profile(profile)
        self._sync_chats_for_profile(saved)
        return {"ok": True, "profile": saved}

    def save_agent_profile_override(self, bundled_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise ValueError("patch must be an object")
        saved = _pa.save_agent_profile_override(bundled_id, patch)
        self._sync_chats_for_profile(saved)
        return {"ok": True, "profile": saved}

    def _sync_chats_for_profile(self, profile: dict[str, Any]) -> None:
        """Keep chat ducky_name in sync when a library profile is renamed (by profile_id)."""
        pid = str(profile.get("id") or "").strip()
        name = str(profile.get("name") or "").strip()
        if not pid or not name:
            return
        try:
            from frontend.ui_web.project_chats import (
                list_all_conversation_metadata,
                load_conversation,
                save_conversation,
            )

            for meta in _pa.list_all_conversation_metadata():
                if str(getattr(meta, "profile_id", "") or "").strip() != pid:
                    continue
                if str(getattr(meta, "ducky_name", "") or "").strip() == name:
                    continue
                conv = _pa.load_conversation(meta.id)
                if conv is None:
                    continue
                conv.ducky_name = name
                _pa.save_conversation(conv, touch_updated=False)
                _pa.notify_chats_changed(conv.id, conv.title, conv.folder_id)
        except Exception:
            _pa.logging.getLogger(__name__).exception("sync chats for profile %s failed", pid)

    def delete_agent_profile(self, profile_id: str) -> dict[str, Any]:
        _pa.delete_agent_profile(profile_id)
        return {"ok": True}

    def duplicate_agent_profile(self, profile_id: str) -> dict[str, Any]:
        duplicated = _pa.duplicate_agent_profile(profile_id)
        return {"ok": True, "profile": duplicated}

    def get_agent_profile_editor_catalog(self) -> dict[str, Any]:
        """Duckies editor catalog — never sync-wait on plugin register().

        Same non-blocking pattern as get_uefn_plugin_contributions: return packs/
        builtins/MCP immediately; fill UEFN agent tool rows once plugins_ready.
        """
        from backend.agent.builtin_toolsets import (
            builtin_group_rows,
            get_enabled_builtin_group_ids,
        )
        from backend.mcp_plugins.store import (
            get_enabled_plugin_ids,
            list_mcp_plugins,
            seed_mcp_plugins,
        )
        from backend.skills.store import default_selection_from_settings, list_skill_packs, seed_skill_packs
        from backend.uefn_plugins.host import (
            ensure_plugins_loaded_async,
            plugins_ready,
            uefn_agent_tool_rows,
        )
        from frontend.ui_web.project_chats import all_available_tool_ids

        seed_skill_packs()
        seed_mcp_plugins()
        settings = _pa.PanelSettings.load()
        sel = default_selection_from_settings(settings)
        base = {
            "packs": list_skill_packs(),
            "default_disabled_packs": list(sel.disabled_packs),
            "default_disabled_tool_ids": [],
            "default_enabled_packs": sel.enabled_packs,  # derived (all − denied)
            "default_enabled_subskills": {},
        }
        if plugins_ready():
            return {
                **base,
                "tools": builtin_group_rows() + list_mcp_plugins() + uefn_agent_tool_rows(),
                "default_tool_ids": all_available_tool_ids(),
                "plugins_loading": False,
            }
        # Kick background load; UI refreshes on uefn_plugins_changed.
        ensure_plugins_loaded_async(on_done=self._notify_plugins_ready)
        return {
            **base,
            "tools": builtin_group_rows() + list_mcp_plugins(),
            "default_tool_ids": list(get_enabled_builtin_group_ids())
            + list(get_enabled_plugin_ids()),
            "plugins_loading": True,
        }

    def get_conversation_skills(self, conv_id: str) -> dict[str, Any]:
        from backend.skills.store import list_skill_packs, resolve_conversation_selection, seed_skill_packs
        from backend.mcp_plugins.store import seed_mcp_plugins

        seed_skill_packs()
        seed_mcp_plugins()
        conv = _pa.load_conversation(conv_id)
        settings = _pa.PanelSettings.load()
        packs = list_skill_packs()
        if conv:
            sel = resolve_conversation_selection(conv, settings)
        else:
            from backend.skills.store import default_selection_from_settings

            sel = default_selection_from_settings(settings)
        # For UI: packs with no stored allowlist show every toggleable root as on.
        enabled_subs = dict(sel.enabled_subskills)
        for pack in packs:
            pid = str(pack.get("id") or "")
            if not pid or pid in enabled_subs:
                continue
            if pid in sel.disabled_packs:
                continue
            toggleable = [
                str(s.get("id"))
                for s in (pack.get("subskills") or [])
                if isinstance(s, dict)
                and str(s.get("id") or "").strip()
                and not s.get("parent_id")
                and not s.get("always_on")
            ]
            enabled_subs[pid] = toggleable
        return {
            "disabled_packs": list(sel.disabled_packs),
            "enabled_packs": sel.enabled_packs,
            "enabled_subskills": enabled_subs,
            "enabled_skills": sel.enabled_packs,
            "packs": packs,
            "catalog": packs,
            "primary_filename": "uefn",
        }

    def set_conversation_skills(self, conv_id: str, filenames: list[str]) -> dict[str, Any]:
        enabled = _pa.set_conversation_enabled_skills(conv_id, filenames)
        return {"ok": True, "enabled_skills": enabled, "enabled_packs": enabled}

    def set_conversation_skill_selection(
        self,
        conv_id: str,
        enabled_packs: list[str],
        enabled_subskills: dict[str, list[str]],
    ) -> dict[str, Any]:
        packs, subs = _pa.set_conversation_skill_selection(conv_id, enabled_packs, enabled_subskills)
        return {
            "ok": True,
            "enabled_packs": packs,
            "enabled_subskills": subs,
            "enabled_skills": packs,
        }

    def set_default_enabled_skills(self, filenames: list[str]) -> dict[str, Any]:
        return self.set_default_skill_selection(filenames, {})

    def set_default_skill_selection(
        self,
        enabled_packs: list[str],
        enabled_subskills: dict[str, list[str]],
    ) -> dict[str, Any]:
        """Legacy API — clears the default deny-list (all packs available)."""
        del enabled_packs, enabled_subskills
        from backend.skills.store import merge_selection

        settings = _pa.PanelSettings.load()
        sel = merge_selection(disabled_packs=[])
        settings = _pa.replace(
            settings,
            default_disabled_packs=[],
            default_enabled_packs=[],
            default_enabled_subskills={},
            default_enabled_skills=[],
        )
        settings.save()
        return {
            "ok": True,
            "default_disabled_packs": [],
            "default_enabled_packs": sel.enabled_packs,
            "default_enabled_subskills": {},
            "default_enabled_skills": sel.enabled_packs,
        }

    def set_conversation_ducky_style(
        self,
        conv_id: str,
        ducky_style: str,
        ducky_personality: str | None = None,
    ) -> None:
        _pa.set_conversation_ducky_style(conv_id, ducky_style, ducky_personality=ducky_personality)

    def list_ducky_catalog(self) -> dict[str, Any]:
        from frontend.ui_web.panel_httpd import panel_ui_http_url

        catalog = _pa._list_ducky_catalog()
        base = panel_ui_http_url()
        for item in catalog.get("builtin", []):
            if isinstance(item, dict):
                item["url"] = f"./duckies/{item.get('file', '')}"
        for item in catalog.get("custom", []):
            if isinstance(item, dict):
                duck_id = str(item.get("id", ""))
                slug = duck_id.removeprefix("custom:")
                item["url"] = f"{base}duckies/custom/{slug}.png"
        catalog["custom_base_url"] = base
        return catalog

    def upload_custom_ducky(self, filename: str, png_base64: str) -> dict[str, str]:
        entry = _pa.save_custom_ducky_png(filename, png_base64)
        from frontend.ui_web.panel_httpd import panel_ui_http_url

        base = panel_ui_http_url()
        slug = entry.id.removeprefix("custom:")
        return {
            "id": entry.id,
            "label": entry.label,
            "file": entry.file,
            "kind": entry.kind,
            "url": f"{base}duckies/custom/{slug}.png",
        }

    def delete_custom_ducky(self, style_id: str) -> dict[str, bool]:
        return {"ok": _pa.delete_custom_ducky(style_id)}

    def open_custom_duckies_folder(self) -> None:
        d = _pa.custom_duckies_dir(for_write=True)
        _pa.os.startfile(str(d))  # type: ignore[attr-defined]

    def list_custom_verse_templates(self) -> list[dict[str, Any]]:
        return [entry.to_dict() for entry in _pa.list_custom_verse_templates()]

    def get_custom_verse_template(self, template_id: str) -> dict[str, Any] | None:
        entry = _pa.get_custom_verse_template(template_id)
        return entry.to_dict() if entry else None

    def save_custom_verse_template(
        self,
        name: str,
        icon: str,
        content: str = "",
        template_id: str = "",
        folder: str = "",
        files_json: str = "",
    ) -> dict[str, Any]:
        """Create or update a user Verse template (never Store/plugin templates).

        files_json: optional JSON array of ``{path, content}`` for multi-file packs.
        Pass template_id to update an existing ``custom:…`` template.
        """
        files: Any = None
        raw = (files_json or "").strip()
        if raw:
            try:
                files = _pa.json.loads(raw)
            except _pa.json.JSONDecodeError as exc:
                raise ValueError(f"invalid files_json: {exc}") from exc
        return _pa.save_custom_verse_template(
            name,
            icon,
            content,
            template_id=template_id or "",
            folder=folder or "",
            files=files,
        ).to_dict()

    def delete_custom_verse_template(self, template_id: str) -> dict[str, bool]:
        return {"ok": _pa.delete_custom_verse_template(template_id)}

    def rename_conversation(self, conv_id: str, title: str) -> None:
        conv = _pa.load_conversation(conv_id)
        if conv:
            conv.title = title.strip() or conv.title
            _pa.save_conversation(conv)

    def move_conversation(self, conv_id: str, folder_id: str) -> None:
        if _pa.is_archive_folder_id(folder_id):
            from frontend.ui_web.agent_modes import cancel_agent, is_agent_running

            for target_id in [conv_id, *_pa.conversation_descendant_ids(conv_id)]:
                if is_agent_running(target_id):
                    _pa.cancel_agent(target_id)
        _pa.move_conversation(conv_id, folder_id or "")

    def delete_conversation(self, conv_id: str) -> None:
        from frontend.ui_web.agent_modes import cancel_agent, is_agent_running

        for target_id in [conv_id, *_pa.conversation_descendant_ids(conv_id)]:
            if is_agent_running(target_id):
                _pa.cancel_agent(target_id)
        _pa.delete_conversation(conv_id)

    def apply_sidebar_layout(self, payload: dict[str, Any]) -> None:
        folders = payload.get("folders")
        chats = payload.get("chats")
        if not isinstance(folders, list) or not isinstance(chats, list):
            raise ValueError("layout payload must include folders and chats arrays")
        _pa.apply_sidebar_layout(folders=folders, chats=chats)

    def load_messages(self, conv_id: str) -> list[dict[str, Any]]:
        """The one message-load path: whole conversation as flat UI rows.

        Conversations are small local files (a handful of turns), so there is no
        pagination — `_messages_to_ui` is the single source of truth for how a
        stored conversation becomes the rows the chat renders.
        """
        conv = _pa.load_conversation(conv_id)
        if not conv:
            return []
        return _pa._messages_to_ui(conv)

    def send_message(
        self,
        conv_id: str,
        text: str,
        mode: str,
        model: str,
        file_path: str = "",
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, str]:
        from frontend.ui_web.group_orchestrator import (
            is_group_conversation,
            run_group_turn,
        )

        if file_path:
            before = _pa.load_conversation(conv_id)
            had_path = bool(before and before.file_path)
            _pa.ensure_conversation_file_path(conv_id, file_path)
            if not had_path:
                after = _pa.load_conversation(conv_id)
                if after and after.file_path:
                    _pa.notify_chats_changed(after.id, after.title, after.folder_id, push=self._push)
        # Composer model updates this conversation only — never global settings
        # or any Ducky profile favorite_models.
        turn_model = (model or "").strip()
        if turn_model and turn_model.lower() != "default":
            conv = _pa.load_conversation(conv_id)
            if conv is not None:
                from backend.agent.coding_agents.base import normalize_coding_agent
                from backend.agent.model_pricing import resolve_provider_for_model

                model_changed = (conv.model or "").strip() != turn_model
                if model_changed:
                    conv.model = turn_model
                if normalize_coding_agent(getattr(conv, "coding_agent", None) or "ducky") == "ducky":
                    resolved = resolve_provider_for_model(turn_model, conv.provider or "")
                    if resolved and resolved != (conv.provider or "").strip().lower():
                        conv.provider = resolved
                        model_changed = True
                if model_changed:
                    _pa.save_conversation(conv)
        conv = _pa.load_conversation(conv_id)
        # Subagents retired — every non-group chat is composable (group members included).
        if conv is not None and is_group_conversation(conv):
            # Group chats ignore attachments for now — members get a text prompt.
            run_id = run_group_turn(
                conv_id,
                text,
                mode=mode,
                model=model,
                push=self._push,
            )
            return {"run_id": run_id}
        run_id = _pa.run_message(conv_id, text, mode, model, push=self._push, attachments=attachments or [])
        return {"run_id": run_id}

    def resend_last_user_message(
        self,
        conv_id: str,
        text: str,
        mode: str,
        model: str,
        file_path: str = "",
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, str]:
        """Cursor-style edit + resend: rewind to the last user turn and rerun.

        Only the most recent user message is editable, so we never need to map a
        synthetic UI row id back to a stored message — we just drop the last
        ``role == "user"`` entry (and everything after it) and let the normal
        send path append the edited message and start a fresh run.
        """
        conv = _pa.load_conversation(conv_id)
        if not conv:
            return {"run_id": ""}
        # Cursor-style edit mid-answer: cancel the live run, then rewind + rerun.
        if conv_id in _pa.list_running_agents():
            _pa.cancel_agent(conv_id)
            _pa.wait_for_idle(conv_id, 2.0)
            if conv_id in _pa.list_running_agents():
                return {"run_id": ""}
        last_user = next(
            (i for i in range(len(conv.messages) - 1, -1, -1) if conv.messages[i].get("role") == "user"),
            None,
        )
        if last_user is not None:
            conv.messages = conv.messages[:last_user]
            _pa.save_conversation(conv)
        return self.send_message(conv_id, text, mode, model, file_path, attachments)

    def get_context_usage(
        self,
        conv_id: str,
        model: str,
        mode: str = "agent",
        draft: str = "",
        include_content: bool = False,
    ) -> dict[str, Any]:
        from frontend.ui_web.context_tokens import compute_context_usage

        try:
            return compute_context_usage(
                conv_id, model, mode=mode, draft_text=draft, include_content=include_content
            )
        except Exception as exc:
            import logging

            _pa.logging.getLogger(__name__).exception("get_context_usage failed for %s: %s", conv_id, exc)
            from frontend.ui_web.context_tokens import context_limit_for_model

            limit = context_limit_for_model(model) or 0
            return {
                "used_tokens": 0,
                "context_limit": limit,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "breakdown": [],
                "omitted": [],
                "error": str(exc),
            }

    def reset_context(self, conv_id: str, segments: list[str], mode: str = "agent", model: str = "") -> dict[str, Any]:
        from frontend.ui_web.context_control import reset_context

        return reset_context(conv_id, segments, model=model, mode=mode)

    def restore_context(self, conv_id: str, segments: list[str], mode: str = "agent", model: str = "") -> dict[str, Any]:
        from frontend.ui_web.context_control import restore_context

        return restore_context(conv_id, segments, model=model, mode=mode)

    def get_session_files(self, conv_id: str) -> list[dict[str, Any]]:
        from frontend.ui_web.session_files import compute_session_files

        return compute_session_files(conv_id)

    def cancel_agent(self, conv_id: str = "") -> bool:
        from frontend.ui_web.group_orchestrator import cancel_group_run, list_group_running_ids

        if conv_id:
            cancel_group_run(conv_id)
        else:
            for gid in list_group_running_ids():
                cancel_group_run(gid)
        _pa.cancel_agent(conv_id or None)
        return True

    def list_running_agents(self) -> list[str]:
        from frontend.ui_web.group_orchestrator import list_group_running_ids

        ids = list(_pa.list_running_agents())
        for gid in list_group_running_ids():
            if gid not in ids:
                ids.append(gid)
        return ids

    def wait_for_agent_idle(self, conv_id: str, timeout: float = 2.0) -> bool:
        return _pa.wait_for_idle(conv_id, timeout)

    def voice_transcribe_audio(self, b64_audio: str, mime: str = "audio/webm") -> dict[str, Any]:
        """Batch Whisper transcription (prefer via bridge_job_start)."""
        from backend.voice.transcription import transcribe_audio

        return transcribe_audio(str(b64_audio or ""), str(mime or "audio/webm"))

    def voice_create_realtime_token(self) -> dict[str, Any]:
        """Mint an ephemeral Realtime client secret for browser streaming STT."""
        from backend.voice.transcription import create_realtime_token

        return create_realtime_token()

    def voice_summarize_reply(self, assistant_text: str, model: str = "") -> dict[str, Any]:
        """Short spoken summary via the cheap voice model (prefer via bridge_job_start)."""
        from backend.voice.summary import summarize_for_speech

        return summarize_for_speech(str(assistant_text or ""), model=str(model or ""))
