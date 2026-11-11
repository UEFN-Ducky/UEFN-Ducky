"""Folders, conversations, groups, profiles, duckies, send/cancel. Mixin for PanelApi — methods stay on the PyWebView JS object.

Each method is a plain function in ``frontend.ui_web.panel_chats`` that takes the PanelApi
as ``self``; binding it here keeps its name and signature on the JS API.
"""

from __future__ import annotations

from frontend.ui_web.panel_chats import conversations, folders, groups, messages, permissions, profiles, send, voice
from frontend.ui_web.panel_chats.send import _sync_composer_selection  # noqa: F401
from frontend.ui_web.panel_chats.messages import TOOL_RESULT_CAP, _cap_tool_results  # noqa: F401
from frontend.ui_web.panel_chats.groups import _copy_name, _leader_agent_ok  # noqa: F401
from frontend.ui_web.panel_chats.conversations import _conversation_row  # noqa: F401
from frontend.ui_web.panel_chats.folders import _FOLDER_WRITE_LOCK, GLOBAL_SLUG  # noqa: F401


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
    load_tool_file_edits = messages.load_tool_file_edits
    get_context_usage = messages.get_context_usage
    reset_context = messages.reset_context
    restore_context = messages.restore_context
    get_session_files = messages.get_session_files

    # -- send and stop (panel_chats/send.py) ------------------------------------
    send_message = send.send_message
    run_held_messages = send.run_held_messages
    held_for_model = send.held_for_model
    continue_interrupted = send.continue_interrupted
    resend_last_user_message = send.resend_last_user_message
    rewind_to_message = send.rewind_to_message
    cancel_agent = send.cancel_agent
    steer_message = send.steer_message
    list_running_agents = send.list_running_agents
    wait_for_agent_idle = send.wait_for_agent_idle

    # -- voice (panel_chats/voice.py) -------------------------------------------
    voice_transcribe_audio = voice.voice_transcribe_audio
    voice_create_realtime_token = voice.voice_create_realtime_token
    voice_win_tts_start = voice.voice_win_tts_start
    voice_win_tts_voices_start = voice.voice_win_tts_voices_start
    voice_win_tts_poll = voice.voice_win_tts_poll
    voice_win_stt_prewarm = voice.voice_win_stt_prewarm
    voice_win_stt_start = voice.voice_win_stt_start
    voice_win_stt_poll = voice.voice_win_stt_poll
    voice_win_stt_stop = voice.voice_win_stt_stop
    voice_win_stt_cancel = voice.voice_win_stt_cancel
    voice_open_windows_settings = voice.voice_open_windows_settings
    voice_summarize_reply = voice.voice_summarize_reply
