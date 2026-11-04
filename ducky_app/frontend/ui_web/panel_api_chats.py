"""Folders, conversations, groups, profiles, duckies, send/cancel. Mixin for PanelApi — methods stay on the PyWebView JS object."""

from __future__ import annotations

import threading
import uuid
from typing import Any

import frontend.ui_web.panel_api as _pa
from frontend.ui_web.panel_chats import conversations, folders, groups, messages, permissions, profiles
from frontend.ui_web.panel_chats.messages import _NOT_LOADED, TOOL_RESULT_CAP, _cap_tool_results, _page_rows  # noqa: F401
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

    # -- ducky profiles (panel_chats/profiles.py) -------------------------------
    list_agent_profiles = profiles.list_agent_profiles
    archive_agent_profile = profiles.archive_agent_profile
    unarchive_agent_profile = profiles.unarchive_agent_profile
    save_agent_profile = profiles.save_agent_profile
    save_agent_profile_override = profiles.save_agent_profile_override
    _profile_name = staticmethod(profiles._profile_name)
    _sync_chats_for_profile = profiles._sync_chats_for_profile
    delete_agent_profile = profiles.delete_agent_profile
    duplicate_agent_profile = profiles.duplicate_agent_profile
    get_agent_profile_editor_catalog = profiles.get_agent_profile_editor_catalog

    # -- agent approvals (panel_chats/permissions.py) ---------------------------
    set_agent_allow_everything = permissions.set_agent_allow_everything
    get_agent_permissions = permissions.get_agent_permissions
    update_agent_plugin = permissions.update_agent_plugin
    set_agent_permission_mode = permissions.set_agent_permission_mode
    remove_agent_permission_rule = permissions.remove_agent_permission_rule
    clear_agent_permission_rules = permissions.clear_agent_permission_rules
    list_saved_agent_permissions = permissions.list_saved_agent_permissions
    revoke_saved_agent_permission = permissions.revoke_saved_agent_permission

    # -- messages and context (panel_chats/messages.py) -------------------------
    load_messages = messages.load_messages
    load_tool_result = messages.load_tool_result
    get_context_usage = messages.get_context_usage
    reset_context = messages.reset_context
    restore_context = messages.restore_context
    get_session_files = messages.get_session_files

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
