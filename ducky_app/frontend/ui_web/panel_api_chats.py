"""Folders, conversations, groups, profiles, duckies, send/cancel. Mixin for PanelApi — methods stay on the PyWebView JS object."""

from __future__ import annotations

import threading
import uuid
from typing import Any

import frontend.ui_web.panel_api as _pa
from frontend.ui_web.panel_chats import conversations, folders, groups
from frontend.ui_web.panel_chats.groups import _copy_name, _leader_agent_ok  # noqa: F401
from frontend.ui_web.panel_chats.conversations import _conversation_row  # noqa: F401
from frontend.ui_web.panel_chats.folders import _FOLDER_WRITE_LOCK, GLOBAL_SLUG  # noqa: F401


def _sync_composer_selection(conv_id: str, model: str, coding_agent: str = "") -> None:
    """Apply the composer's model + backend to the chat before a turn starts.

    The picker's own save can lose to a still-running turn that saves its stale
    copy of the chat, so the send is authoritative. Updates this conversation
    only — never global settings or a Ducky profile's favorite_models.
    """
    from backend.agent.coding_agents.base import normalize_coding_agent
    from backend.agent.model_pricing import resolve_provider_for_model

    turn_model = (model or "").strip()
    if turn_model.lower() == "default":
        turn_model = ""
    agent = normalize_coding_agent(coding_agent) if (coding_agent or "").strip() else ""
    if not turn_model and not agent:
        return
    conv = _pa.load_conversation(conv_id)
    if conv is None:
        return
    changed = False
    current = normalize_coding_agent(getattr(conv, "coding_agent", None) or "ducky")
    if agent and agent != current:
        conv.coding_agent = current = agent
        if agent != "ducky":
            conv.provider = ""
        changed = True
    if turn_model and (conv.model or "").strip() != turn_model:
        conv.model = turn_model
        changed = True
    if turn_model and current == "ducky":
        resolved = resolve_provider_for_model(turn_model, conv.provider or "")
        if resolved and resolved != (conv.provider or "").strip().lower():
            conv.provider = resolved
            changed = True
    if changed:
        _pa.save_conversation(conv)


# A tool card's preview; past it the card asks load_tool_result for the rest.
TOOL_RESULT_CAP = 8_000


def _cap_tool_results(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        tool = row.get("tool")
        result = tool.get("result") if isinstance(tool, dict) else None
        if isinstance(result, str) and len(result) > TOOL_RESULT_CAP:
            row["tool"] = {**tool, "result": result[:TOOL_RESULT_CAP], "resultTruncated": True,
                           "resultBytes": len(result.encode("utf-8"))}


# The stand-in for a message a page doesn't show: _messages_to_ui walks from the end and
# stops once it has enough rows, so it is never converted (a page that reaches it widens).
_NOT_LOADED: dict[str, Any] = {"role": "user", "text": ""}


def _page_rows(conv_id: str, before_id: Any, limit: int, live: bool) -> list[dict[str, Any]] | None:
    """The ``limit`` rows before ``before_id`` (the newest without it), opening only the
    messages they come from. ``load_conversation`` opened every message of the chat, one
    DPAPI call each: a 735-message chat out of the opened-row cache took 0.45 s for its
    first page on an idle PC (Nov 1 2026), and the cache keeps 8 chats. None: use the
    whole load (file store, or a save of this chat still queued for the writer)."""
    from frontend.ui_web import project_chats as pc
    from frontend.ui_web.chat_rows import ROW_ID_STEP

    writer = pc._WRITER
    if not pc._use_db() or pc._queued(conv_id) is not None or (writer is not None and writer.checkpoint_waits(conv_id)):
        return None
    conv = pc.load_conversation_meta(conv_id)
    if conv is None:
        return []
    repo = pc._repo()
    stop = None if before_id is None else int(before_id) // ROW_ID_STEP + 1
    want = max(16, limit // 4)  # assistant turns give many rows each
    while True:
        _, count = repo.messages_range(conv_id, 0, 0)
        end = count if stop is None else min(stop, count)
        start = max(0, end - want)
        got, count = repo.messages_range(conv_id, start, end)
        messages: list[Any] = [_NOT_LOADED] * count
        for seq, message in got:
            if seq < count:
                messages[seq] = message
        conv.messages = messages
        rows = _pa._messages_to_ui(conv, live=live, before_id=None if before_id is None else int(before_id), limit=limit)
        if start == 0 or (rows and rows[0]["id"] >= start * ROW_ID_STEP):
            return rows
        want *= 4


class PanelApiChatsMixin:
    # -- folders and chat placement (panel_chats/folders.py) --------------------
    _folder_sidebar_row = staticmethod(folders._folder_sidebar_row)
    _one_list = staticmethod(folders._one_list)
    list_folders = folders.list_folders
    list_conversations = folders.list_conversations
    list_all_conversations = folders.list_all_conversations
    list_conversations_for_file = folders.list_conversations_for_file
    _sidebar_context_tokens = staticmethod(folders._sidebar_context_tokens)
    _conversation_sidebar_row = staticmethod(folders._conversation_sidebar_row)
    create_folder = folders.create_folder
    _folder_root = folders._folder_root
    rename_folder = folders.rename_folder
    delete_folder = folders.delete_folder
    rename_conversation = folders.rename_conversation
    move_chats_to_project = folders.move_chats_to_project
    _chat_root = folders._chat_root
    move_conversation = folders.move_conversation
    delete_conversation = folders.delete_conversation
    _layout_project_root = staticmethod(folders._layout_project_root)
    apply_sidebar_layout = folders.apply_sidebar_layout

    # -- one chat's setup (panel_chats/conversations.py) ------------------------
    chat_tool_view = conversations.chat_tool_view
    create_conversation = conversations.create_conversation
    finish_first_run_setup = conversations.finish_first_run_setup
    _new_conversation = conversations._new_conversation
    apply_ducky_config = conversations.apply_ducky_config
    save_chat_file = conversations.save_chat_file
    set_muted = conversations.set_muted
    get_conversation_skills = conversations.get_conversation_skills
    set_conversation_skills = conversations.set_conversation_skills
    set_conversation_skill_selection = conversations.set_conversation_skill_selection
    set_default_enabled_skills = conversations.set_default_enabled_skills
    set_default_skill_selection = conversations.set_default_skill_selection
    set_conversation_ducky_style = conversations.set_conversation_ducky_style
    list_ducky_catalog = conversations.list_ducky_catalog
    upload_custom_ducky = conversations.upload_custom_ducky
    delete_custom_ducky = conversations.delete_custom_ducky
    open_custom_duckies_folder = conversations.open_custom_duckies_folder
    list_custom_verse_templates = conversations.list_custom_verse_templates
    get_custom_verse_template = conversations.get_custom_verse_template
    save_custom_verse_template = conversations.save_custom_verse_template
    delete_custom_verse_template = conversations.delete_custom_verse_template

    # -- groups, rosters and lanes (panel_chats/groups.py) ----------------------
    group_create = groups.group_create
    _group_create_locked = groups._group_create_locked
    group_find_or_create = groups.group_find_or_create
    group_seat_profile = groups.group_seat_profile
    _agent_api_key_refusal = staticmethod(groups._agent_api_key_refusal)
    group_invite = groups.group_invite
    post_team_message = groups.post_team_message
    group_set_leader = groups.group_set_leader
    group_add_member = groups.group_add_member
    _leads_home_group = staticmethod(groups._leads_home_group)
    _lane_row_in_other_group = staticmethod(groups._lane_row_in_other_group)
    group_set_member_model = groups.group_set_member_model
    _save_roster = staticmethod(groups._save_roster)
    group_set_member_lane = groups.group_set_member_lane
    group_lane_authorities = groups.group_lane_authorities
    group_get_lanes = groups.group_get_lanes
    group_check_lane = groups.group_check_lane
    group_remove = groups.group_remove
    group_members = groups.group_members
    _filed_with_group = staticmethod(groups._filed_with_group)
    group_stop = groups.group_stop

    def list_agent_profiles(self) -> dict[str, Any]:
        profiles = _pa.list_agent_profiles()
        return {
            "profiles": profiles,
            "archived_profiles": _pa.list_archived_agent_profiles(),
            "template_profiles": _pa.list_bundled_agent_profile_templates(),
            "blank_profile_id": _pa.BLANK_PROFILE_ID,
        }

    def archive_agent_profile(self, profile_id: str) -> dict[str, Any]:
        saved = _pa.archive_agent_profile(profile_id)
        return {"ok": True, "profile": saved}

    def unarchive_agent_profile(self, profile_id: str) -> dict[str, Any]:
        saved = _pa.unarchive_agent_profile(profile_id)
        return {"ok": True, "profile": saved}

    def save_agent_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(profile, dict):
            raise ValueError("profile must be an object")
        old = self._profile_name(str(profile.get("id") or ""))
        saved = _pa.save_agent_profile(profile)
        self._sync_chats_for_profile(saved, old)
        return {"ok": True, "profile": saved}

    def save_agent_profile_override(self, bundled_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise ValueError("patch must be an object")
        old = self._profile_name(bundled_id)
        saved = _pa.save_agent_profile_override(bundled_id, patch)
        self._sync_chats_for_profile(saved, old)
        return {"ok": True, "profile": saved}

    @staticmethod
    def _profile_name(profile_id: str) -> str:
        from frontend.agent_profiles import get_agent_profile

        try:
            return str((get_agent_profile(profile_id) or {}).get("name") or "").strip() if profile_id else ""
        except Exception:  # noqa: BLE001 - a new profile has no old name
            return ""

    def _sync_chats_for_profile(self, profile: dict[str, Any], old_name: str = "") -> None:
        """A renamed library profile renames the chats still called by its old name (and its
        numbered copies, "Verse Coder 2"). Seats named by role ("Search lead") keep their name."""
        pid = str(profile.get("id") or "").strip()
        name = str(profile.get("name") or "").strip()
        old = (old_name or "").strip()
        if not pid or not name or not old or old == name:
            return
        try:

            for meta in _pa.list_all_conversation_metadata():
                if str(getattr(meta, "profile_id", "") or "").strip() != pid:
                    continue
                current = str(getattr(meta, "ducky_name", "") or "").strip()
                suffix = current[len(old):]
                if not current.startswith(old) or (suffix and not suffix.strip().isdigit()):
                    continue
                conv = _pa.load_conversation(meta.id)
                if conv is None:
                    continue
                conv.ducky_name = name + suffix
                _pa.save_conversation(conv, touch_updated=False)
                _pa.notify_chats_changed(conv.id, conv.title, conv.folder_id, open_tab=False)
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

    def set_agent_allow_everything(self, conv_id: str, on: bool) -> dict[str, Any]:
        """The chat's own "Allow everything" (approval cards) on or off."""
        from backend.tools.panel.permission_prompt import allow_state, set_allow_everything

        set_allow_everything(str(conv_id or ""), bool(on))
        return {"ok": True, **allow_state(str(conv_id or ""))}

    def get_agent_permissions(self, conv_id: str, agent: str = "") -> dict[str, Any]:
        """The chat's approval mode, the modes its agent can use and the rules it allowed."""
        from backend.tools.panel.permission_prompt import chat_permissions

        return chat_permissions(str(conv_id or ""), str(agent or ""))

    def update_agent_plugin(self, conv_id: str, agent: str = "") -> dict[str, Any]:
        """The pop-up's Update: install the Store's newest plugin for this agent, then its new modes."""
        from backend.tools.panel import permission_prompt as permissions

        conv_id = str(conv_id or "")
        agent_id = str(agent or "") or permissions.chat_permissions(conv_id)["agent"]
        permissions.update_agent_plugin(agent_id)
        return permissions.chat_permissions(conv_id, agent_id)

    def set_agent_permission_mode(self, conv_id: str, mode: str, agent: str = "") -> dict[str, Any]:
        """Pick "ask", "edits" or "all" for this chat; its next turn uses it."""
        from backend.tools.panel import permission_prompt as permissions

        conv_id, agent = str(conv_id or ""), str(agent or "")
        row = next((m for m in permissions.chat_permissions(conv_id, agent)["modes"] if m["id"] == mode), None)
        if row is None:
            raise ValueError(f"Unknown permission mode: {mode!r}")
        if not row["available"]:
            raise ValueError(row["reason"])
        permissions.set_permission_mode(conv_id, mode)
        state = permissions.chat_permissions(conv_id, agent)
        if state["mode"] != mode:
            raise ValueError("Could not save the permission change.")
        return state

    def remove_agent_permission_rule(self, conv_id: str, rule: str, agent: str = "") -> dict[str, Any]:
        """Forget one "always allow" rule of this chat."""
        from backend.tools.panel import permission_prompt as permissions

        conv_id = str(conv_id or "")
        permissions.forget_rule(conv_id, str(rule or ""))
        if rule in permissions._rules(conv_id):
            raise ValueError("Could not save the permission change.")
        return permissions.chat_permissions(conv_id, str(agent or ""))

    def clear_agent_permission_rules(self, conv_id: str, agent: str = "") -> dict[str, Any]:
        """Forget every "always allow" rule of this chat; its mode stays."""
        from backend.tools.panel import permission_prompt as permissions

        conv_id = str(conv_id or "")
        permissions.clear_rules(conv_id)
        state = permissions.chat_permissions(conv_id, str(agent or ""))
        if state["rules"]:
            raise ValueError("Could not save the permission change.")
        return state

    def list_saved_agent_permissions(self) -> list[dict[str, str]]:
        """All remembered chat approvals, including individual command/file rules."""
        from backend.tools.panel import permission_prompt as permissions

        if permissions._rules_in_db():
            from backend.store.repos import chats, kv

            docs = kv.list_docs("workspace_state", "agent_allow:")
            entries = []
            for key, rules in docs.items():
                conv_id = key.removeprefix("agent_allow:")
                conv = chats.conv_get(conv_id, with_messages=False)
                if conv and isinstance(rules, list):
                    entries.append((conv_id, str(conv.get("title") or "Chat"), rules))
        else:
            entries = [
                (conv.id, conv.title or "Chat", list(getattr(conv, "agent_allow_rules", None) or []))
                for _, conv in _pa.iter_conversations_by_project()
            ]
        return [
            {"conv_id": conv_id, "title": title, "rule": rule,
             "label": "Allow everything in this chat" if rule == "*" else rule}
            for conv_id, title, rules in entries for rule in rules if isinstance(rule, str) and rule
        ]

    def revoke_saved_agent_permission(self, conv_id: str, rule: str) -> dict[str, bool]:
        """Human Settings action: remove one existing approval, never grant access."""
        from backend.tools.panel import permission_prompt as permissions

        rules = permissions._rules(conv_id)
        remaining = [saved for saved in rules if saved != rule]
        permissions._store_rules(conv_id, remaining)
        if permissions._rules(conv_id) != remaining:
            raise ValueError("Could not save the permission change.")
        return {"ok": True}

    def load_messages(
        self, conv_id: str, before_id: int | None = None, limit: int | None = None, full_results: bool = False
    ) -> list[dict[str, Any]]:
        """The one message-load path: conversation as flat UI rows.

        `_messages_to_ui` is the single source of truth for how a stored
        conversation becomes rows; row ids are message index * 1000 + row index,
        so a page (``limit`` newest rows, or the ``limit`` rows before
        ``before_id``) converts only the messages it shows, newest first.
        Without arguments the whole conversation comes back, as before.

        A tool result over ``TOOL_RESULT_CAP`` chars is cut, with ``resultTruncated``
        and ``resultBytes`` on the row (``load_tool_result`` has it whole): 40 rows over
        32 KB were 22% of a 7 MB chat. ``full_results`` keeps them whole (account mirror).
        """
        started = _pa.time.monotonic()
        try:
            live = conv_id in self.list_running_agents()
            rows = None if limit is None else _page_rows(conv_id, before_id, int(limit), live)
            if rows is None:
                conv = _pa.load_conversation(conv_id)
                if not conv:
                    return []
                rows = _pa._messages_to_ui(
                    conv,
                    live=live,
                    before_id=None if before_id is None else int(before_id),
                    limit=None if limit is None else int(limit),
                )
        except Exception as exc:
            # A chat that can't be read must leave a trace, not just a spinner.
            _pa.record_error("chat", f"Chat {str(conv_id)[:8]} couldn't be loaded: {type(exc).__name__}: {exc}")
            raise
        if not full_results:
            _cap_tool_results(rows)
        took = _pa.time.monotonic() - started
        if took > 5.0:
            _pa.record_activity("chat", f"Chat {str(conv_id)[:8]} took {took:.1f}s to load ({len(rows)} rows)")
        return rows

    def load_tool_result(self, conv_id: str, row_id: int) -> dict[str, Any]:
        """The whole result of one tool row that ``load_messages`` cut."""
        conv = _pa.load_conversation(conv_id)
        if not conv:
            return {"ok": False, "error": "Chat not found"}
        rows = _pa._messages_to_ui(conv, before_id=int(row_id) + 1, limit=1)
        tool = rows[-1].get("tool") if rows and rows[-1].get("id") == int(row_id) else None
        if not isinstance(tool, dict):
            return {"ok": False, "error": "No tool result in that row"}
        return {"ok": True, "result": str(tool.get("result") or "")}

    def send_message(
        self,
        conv_id: str,
        text: str,
        mode: str,
        model: str,
        file_path: str = "",
        attachments: list[dict[str, Any]] | None = None,
        coding_agent: str = "",
    ) -> dict[str, Any]:
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
                    _pa.notify_chats_changed(
                        after.id, after.title, after.folder_id, push=self._push, open_tab=False
                    )
        _sync_composer_selection(conv_id, model, coding_agent)
        conv = _pa.load_conversation(conv_id)
        # Subagents retired — every non-group chat is composable (group members included).
        if conv is not None and is_group_conversation(conv):
            run_id = run_group_turn(
                conv_id,
                text,
                mode=mode,
                model=model,
                push=self._push,
                attachments=attachments or [],
            )
            return {"run_id": run_id}
        run_id = _pa.run_message(conv_id, text, mode, model, push=self._push, attachments=attachments or [])
        from frontend.ui_web.agent_modes import HELD_FOR_MODEL

        if run_id == HELD_FOR_MODEL:
            return {"run_id": "", "held_for_model": "1"}
        if run_id == "queued":  # the chat runs: the send waits as a steer / next turn, never a Stop
            return {"ok": True, "queued": True, "note": "Sent — the agent takes it at its next step"}
        return {"run_id": run_id}

    def run_held_messages(self, conv_id: str, model: str, coding_agent: str = "") -> dict[str, Any]:
        """The composer picked a model for a chat whose messages waited for one: save the
        pick the way a send does, then run them in order."""
        from frontend.ui_web.agent_modes import release_held_for_model

        if not (model or "").strip() or (model or "").strip().lower() == "default":
            return {"ok": False, "error": "Pick a model first."}
        _sync_composer_selection(conv_id, model, coding_agent)
        return {"ok": True, "ran": release_held_for_model(conv_id)}

    def held_for_model(self, conv_id: str) -> dict[str, Any]:
        """How many messages this chat keeps until a model is picked."""
        from frontend.ui_web.agent_modes import held_for_model

        return {"ok": True, "held": held_for_model(conv_id)}

    def continue_interrupted(
        self,
        conv_id: str,
        mode: str,
        model: str,
        file_path: str = "",
        coding_agent: str = "",
    ) -> dict[str, str]:
        """Resume the last interrupted turn without appending a user message."""
        from frontend.ui_web.group_orchestrator import is_group_conversation

        if file_path:
            _pa.ensure_conversation_file_path(conv_id, file_path)
        _sync_composer_selection(conv_id, model, coding_agent)
        conv = _pa.load_conversation(conv_id)
        if conv is None or (conv is not None and is_group_conversation(conv)):
            return {"run_id": ""}
        run_id = _pa.run_message(conv_id, "", mode, model, push=self._push, resume=True)
        return {"run_id": run_id}

    def resend_last_user_message(
        self,
        conv_id: str,
        text: str,
        mode: str,
        model: str,
        file_path: str = "",
        attachments: list[dict[str, Any]] | None = None,
        coding_agent: str = "",
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
        return self.send_message(conv_id, text, mode, model, file_path, attachments, coding_agent)

    def rewind_to_message(self, conv_id: str, row_id: int, text: str = "") -> dict[str, Any]:
        """Restart from here: erase this user message and everything after it, as if never sent.

        ``row_id`` is the message's UI row id (stored index * ROW_ID_STEP); ``text`` must still
        match it, so a chat changed meanwhile is refused instead of cut in the wrong place. A
        coding agent's resumed session still holds the erased turns: it is dropped, and the
        next turn starts a new one from the kept messages. The composer takes the text back.
        """
        from frontend.ui_web.chat_rows import ROW_ID_STEP

        conv = _pa.load_conversation(conv_id)
        if not conv:
            return {"ok": False, "error": "Chat not found."}
        if conv_id in _pa.list_running_agents():
            _pa.cancel_agent(conv_id)
            _pa.wait_for_idle(conv_id, 2.0)
            if conv_id in _pa.list_running_agents():
                return {"ok": False, "error": "The agent is still stopping. Try again in a moment."}
            conv = _pa.load_conversation(conv_id) or conv
        try:
            index = int(row_id) // ROW_ID_STEP
        except (TypeError, ValueError):
            return {"ok": False, "error": "Unknown message."}
        msg = conv.messages[index] if 0 <= index < len(conv.messages) else None
        stored = str((msg or {}).get("text", (msg or {}).get("content", "")) or "") if isinstance(msg, dict) else ""
        if not isinstance(msg, dict) or msg.get("role") != "user" or stored.strip() != (text or "").strip():
            return {"ok": False, "error": "This chat changed. Reload it and try again."}
        conv.messages = conv.messages[:index]
        if str(getattr(conv, "upstream_session_id", "") or "").strip():
            from backend.agent.coding_agents.session_refresh import mark_rewound

            mark_rewound(conv)
        try:
            from backend.agent.prompt_cache import invalidate_conv_cache

            invalidate_conv_cache(conv)
        except Exception:
            pass
        _pa.save_conversation(conv)
        return {"ok": True, "kept": index}

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

    def steer_message(self, conv_id: str, steer_id: str, text: str) -> dict[str, Any]:
        """Send now on a queued message: the running turn takes it at its next tool call
        (frontend/ui_web/steer.py). ok False: no turn runs, send it as a normal message."""
        from frontend.ui_web import steer

        return {"ok": steer.post(conv_id, steer_id, text)}

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

    def voice_win_tts_start(self, text: str, voice_id: str = "") -> dict[str, Any]:
        from backend.voice.windows_tts import start

        return start(str(text or ""), str(voice_id or ""))

    def voice_win_tts_voices_start(self) -> dict[str, Any]:
        from backend.voice.windows_tts import voices_start

        return voices_start()

    def voice_win_tts_poll(self, job_id: str) -> dict[str, Any]:
        from backend.voice.windows_tts import poll

        return poll(str(job_id or ""))

    def voice_win_stt_prewarm(self) -> dict[str, Any]:
        """Spawn the Windows speech helper early so the first mic press starts fast."""
        from backend.voice.windows_speech import prewarm

        return prewarm()

    def voice_win_stt_start(self, lang: str = "") -> dict[str, Any]:
        """Start Windows dictation (desktop mic, no key); returns session + poll cursor."""
        from backend.voice.windows_speech import start_session

        return start_session(str(lang or ""))

    def voice_win_stt_poll(self, session: str, cursor: int = 0, wait_ms: int = 400) -> dict[str, Any]:
        """Long-poll Windows dictation events (interim / final / ended)."""
        from backend.voice.windows_speech import poll_session

        return poll_session(str(session or ""), cursor, wait_ms)

    def voice_win_stt_stop(self, session: str) -> dict[str, Any]:
        from backend.voice.windows_speech import stop_session

        return stop_session(str(session or ""))

    def voice_win_stt_cancel(self, session: str) -> dict[str, Any]:
        from backend.voice.windows_speech import cancel_session

        return cancel_session(str(session or ""))

    def voice_open_windows_settings(self, page: str) -> dict[str, Any]:
        """Open Windows Settings → speech / microphone privacy so the user can turn it on."""
        from backend.voice.windows_speech import open_windows_settings

        return open_windows_settings(str(page or ""))

    def voice_summarize_reply(self, assistant_text: str, model: str = "") -> dict[str, Any]:
        """Short spoken summary via the cheap voice model (prefer via bridge_job_start)."""
        from backend.voice.summary import summarize_for_speech

        return summarize_for_speech(str(assistant_text or ""), model=str(model or ""))
