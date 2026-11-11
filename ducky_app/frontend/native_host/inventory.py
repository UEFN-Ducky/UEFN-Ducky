"""What the native launcher must cover: every js_api method and pywebview window call.

INVENTORY lists every public method on ``PanelApi`` (the page's ``window.pywebview.api``).
WINDOW_INVENTORY lists the pywebview window, dialog, event and tray calls the Python
window code makes. Both are scanned from the source with ``ast`` (no imports, so the
scan never starts pywebview), and the tests fail when the source grows a name the
lists don't have.
"""

from __future__ import annotations

import ast
from pathlib import Path

DUCKY_APP = Path(__file__).resolve().parents[2]
PANEL_API = DUCKY_APP / "frontend" / "ui_web" / "panel_api.py"


def _module_file(module: str) -> Path:
    return DUCKY_APP.joinpath(*module.split(".")).with_suffix(".py")


def _public_methods(cls: ast.ClassDef) -> list[str]:
    """Public ``def``s plus ``name = module.func`` bindings (how PanelApiChatsMixin is built)."""
    names: list[str] = []
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign) and isinstance(node.value, (ast.Attribute, ast.Name, ast.Call)):
            names.extend(t.id for t in node.targets if isinstance(t, ast.Name))
    return [n for n in names if not n.startswith("_") and not n.isupper()]


def _find_class(tree: ast.Module, name: str) -> ast.ClassDef | None:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def scan_js_api(panel_api: Path = PANEL_API) -> dict[str, str]:
    """{method name: defining class} for PanelApi and its mixins, read with ast."""
    tree = ast.parse(panel_api.read_text(encoding="utf-8"))
    imports: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imports[alias.asname or alias.name] = node.module
    root = _find_class(tree, "PanelApi")
    if root is None:
        raise LookupError("class PanelApi not found in panel_api.py")
    found: dict[str, str] = {}
    for base in root.bases:
        if not isinstance(base, ast.Name):
            continue
        module = imports.get(base.id)
        if not module:
            continue
        cls = _find_class(ast.parse(_module_file(module).read_text(encoding="utf-8")), base.id)
        if cls is None:
            continue
        for name in _public_methods(cls):
            found.setdefault(name, base.id)
    for name in _public_methods(root):
        found[name] = "PanelApi"  # PanelApi's own definition wins over a mixin's
    return found


# --- js_api methods, by the class that defines them --------------------------------
# A new public method on PanelApi or a mixin fails test_inventory until it is listed here.
_JS_API: dict[str, str] = {
    "PanelApi": """
        answer_late_question get_listener_status get_restore_tabs notify_activity report_open_tabs
        report_ui_perf restart_listener set_visible_chats set_visible_terminals ui_rpc_ack
        ui_rpc_active ui_rpc_claim ui_rpc_pending_questions ui_rpc_respond
    """,
    "PanelApiAgentsMixin": "agent_broker_call agent_inbox agent_send agent_send_notice",
    "PanelApiWindowMixin": """
        adopt_tab_into_this_focus_window appdata_children appdata_clear appdata_clear_caches
        appdata_delete appdata_delete_project appdata_open appdata_overview appdata_projects
        appdata_sweep apply_auto_update_now apply_update begin_native_window_move
        begin_native_window_resize bind_tk_root bind_window browser_clear_browsing_data
        browser_pane_close browser_pane_command browser_pane_hide_all browser_pane_list
        browser_pane_navigate browser_pane_open browser_pane_set_bounds browser_pane_snapshot
        browser_pane_state browser_runtime_info browser_site_security cache_font cancel_update
        capture_list_shield check_auto_update_now claim_tab close_all_focus_windows
        close_focus_window close_this_window close_uefn crash_report_delete crash_report_send
        crash_reports direct_rtc_answer direct_rtc_connect direct_rtc_report
        dock_focus_window_beside_main exit_all focus_tab get_app_patch_notes
        get_app_update_status get_auto_update_status get_editor_workspace get_header_visibility
        get_install_info get_sidebar_only_saved_bounds get_tab_owner get_update_progress
        get_version get_whats_new get_window_bounds get_workspace_dock hide_window
        is_focus_window is_window_maximized launch_uefn launch_uefn_project launch_uninstall
        list_focus_tab_ids list_window_views mark_whats_new_seen minimize_window
        native_capture_start native_capture_stop native_menu_pick notify_focus_tab_active
        open_appdata open_devtools open_download_page open_external_url open_focus_window
        open_focus_window_at_point open_focus_window_group open_path_in_explorer
        open_project_path_in_explorer pick_project_path raise_focus_window remote_deny_methods
        report_editor_state report_focus_window_layout report_plugin_panel report_ui_crash
        report_ui_error report_update_ui restart_uefn_project restore_focus_windows
        return_tab_to_main rtc_event rtc_reply rtc_signal run_network_test save_editor_workspace
        save_header_visibility save_workspace_dock search_query search_rebuild search_status
        set_sidebar_only_layout set_window_bounds snip_screen store_action store_overview
        store_table_preview terminal_approve_command terminal_busy terminal_busy_many
        terminal_hold terminal_kill terminal_let_go terminal_list terminal_pending_commands
        terminal_read_output terminal_reject_command terminal_request_command terminal_resize
        terminal_spawn terminal_write toggle_maximize uses_native_window_chrome window_box
        window_input window_spotlight_replay window_text_focus
    """,
    "PanelApiStoreMixin": """
        bridge_job_poll bridge_job_start create_mcp_plugin create_mcp_server create_skill
        create_skill_node create_skill_pack create_subskill delete_mcp_plugin delete_mcp_server
        delete_skill delete_skill_pack delete_subskill draft_skill_pack draft_subskill
        ducky_link_call ducky_link_list ducky_link_open ducky_link_rtc_connect ducky_sync_copy
        ducky_sync_forget ducky_sync_list ducky_sync_now duckyos_agent_caps
        duckyos_agent_caps_set duckyos_cancel_login duckyos_get_status duckyos_list_pcs
        duckyos_login duckyos_logout duckyos_open_admin duckyos_open_site duckyos_revoke_pc
        duckyos_store_catalog duckyos_store_download duckyos_store_versions duckyos_submit_code
        ensure_starter_llm_gateways export_skill_pack get_chat_mcp_plugins get_mcp_config
        get_mcp_diagnostics get_mcp_server_connection get_mcp_server_tools
        get_mcp_tools_catalog get_skill_info get_skill_pack_files get_skill_pack_graph
        get_uefn_plugin_allowed_models get_uefn_plugin_contributions
        get_uefn_plugin_secret_labels get_uefn_plugin_secret_status import_skill_pack
        import_skill_pack_bytes install_uefn_plugin_bytes list_mcp_plugins list_mcp_servers
        list_uefn_plugins live_overview live_team_invite live_team_join live_team_leave
        open_mcp_plugins_folder open_skill_pack_folder open_skill_packs_folder
        open_skills_folder open_uefn_plugins_folder plugin_cache_clear plugin_cache_get
        plugin_cache_set plugin_call plugin_data plugin_data_copy_to_space
        plugin_data_open_web plugin_live_hold plugin_live_holds plugin_live_join
        plugin_live_leave plugin_live_member_states plugin_live_send plugin_live_set_my_state
        plugin_live_share plugin_live_shared plugin_live_status plugin_llm_cancel
        plugin_llm_complete plugin_llm_poll plugin_llm_start plugin_panel_error
        plugin_prefs_get_all plugin_prefs_set plugin_prefs_set_all plugin_scope_activity
        plugin_scope_ask plugin_scope_break plugin_scope_choices plugin_scope_drop
        plugin_scope_history plugin_scope_lock plugin_scope_mode plugin_scope_pull
        plugin_scope_push plugin_scope_resolve plugin_scope_restore plugin_scope_retry
        plugin_scope_save_unsent plugin_scope_set plugin_scope_set_all plugin_scope_status
        plugin_scope_sync plugin_scope_unlock plugin_team_overview plugin_translate_batch_poll
        plugin_translate_batch_start plugin_tts_poll plugin_tts_start plugin_tts_voices_poll
        plugin_tts_voices_start prompt_save_skill_pack_export reload_uefn_plugin
        remote_set_enabled remote_sign_out_all remote_status reset_skill reset_skill_pack
        save_first_run_progress save_mcp_server_connection save_pack_manifest save_skill save_skill_file
        save_skill_layout save_skill_node save_subskill set_chat_mcp_plugins set_mcp_config
        set_mcp_plugin_enabled set_mcp_server_enabled set_skill_pack_enabled
        set_uefn_plugin_allowed_models set_uefn_plugin_enabled set_uefn_plugin_secret
        space_reset_settle starter_llm_onboard_pending starter_setup_status test_mcp_plugin
        test_mcp_server test_uefn_plugin_secret uninstall_uefn_plugin
    """,
    "PanelApiChatsMixin": """
        apply_ducky_config apply_sidebar_layout archive_agent_profile cancel_agent chat_tool_view
        clear_agent_permission_rules continue_interrupted create_conversation create_folder
        delete_agent_profile delete_conversation delete_custom_ducky
        delete_custom_verse_template delete_folder duplicate_agent_profile
        finish_first_run_setup get_agent_permissions get_agent_profile_editor_catalog
        get_context_usage get_conversation_skills get_custom_verse_template get_session_files
        group_add_member group_check_lane group_create group_find_or_create group_get_lanes
        group_invite group_lane_authorities group_members group_remove group_seat_profile
        group_set_leader group_set_member_lane group_set_member_model group_stop
        held_for_model list_agent_profiles list_all_conversations list_conversations
        list_conversations_for_file list_custom_verse_templates list_ducky_catalog
        list_folders list_running_agents list_saved_agent_permissions load_messages
        load_tool_file_edits load_tool_result move_chats_to_project move_conversation open_custom_duckies_folder
        post_team_message remove_agent_permission_rule rename_conversation rename_folder
        resend_last_user_message reset_context restore_context revoke_saved_agent_permission
        rewind_to_message run_held_messages save_agent_profile save_agent_profile_override
        save_chat_file save_custom_verse_template send_message set_agent_allow_everything
        set_agent_permission_mode set_conversation_ducky_style
        set_conversation_skill_selection set_conversation_skills set_default_enabled_skills
        set_default_skill_selection set_muted steer_message unarchive_agent_profile
        update_agent_plugin upload_custom_ducky voice_create_realtime_token
        voice_open_windows_settings voice_summarize_reply voice_transcribe_audio
        voice_win_stt_cancel voice_win_stt_poll voice_win_stt_prewarm voice_win_stt_start
        voice_win_stt_stop voice_win_tts_poll voice_win_tts_start voice_win_tts_voices_start
        wait_for_agent_idle
    """,
    "PanelApiProjectMixin": """
        apply_all_ides apply_ide archive_changesets classify_project_file clear_changesets
        compile_verse_project connect_verse_workflow consume_pending_deep_links
        consume_pending_open_files copy_project_entry create_project_file create_project_folder
        create_project_verse_file delete_changeset_entries delete_project_entry
        delete_recent_project deploy deploy_all_projects disconnect_verse_workflow
        export_changeset fingerprint_project_dirs get_changeset get_changeset_entry_contents
        get_ide_statuses get_project_info get_urc_status get_verse_lsp_status
        get_verse_workflow_status import_external_entries inspect_project_folder
        is_verse_editor_enabled list_changesets list_file_history list_known_projects
        list_plugin_references list_project_file_paths list_project_files list_recent_projects
        list_workspace_roots load_verse_diagnostics_cache move_project_entry
        open_asset_in_uefn open_project_file project_file_media_url push_verse_changes
        read_file_history_entry read_import_drop_target read_project_file
        record_external_file_change rename_project_entry replace_workspace
        restore_trashed_entry revert_changeset revert_changeset_entry save_verse_file_cache
        scan_verse_diagnostics search_workspace set_import_drop_target set_project_root
        snapshot_file_history start_verse_lsp stat_project_file stop_verse_diagnostics_scan
        stop_verse_lsp test_ide tester_create_chat tester_list_devices tester_list_tests
        tester_results tester_run_tests tester_scaffold tester_simulate urc_commit urc_push
        write_project_file
    """,
    "PanelApiSettingsMixin": """
        add_gateway_account add_task_phase adopt_default_model build_task_handoff
        clear_chat_context_summary clear_errors clear_log coding_agent_login
        coding_agent_login_cancel coding_agent_login_status coding_agent_login_submit
        coding_agent_logout compress_chat_context copy_plan copy_support_dump copy_text
        create_plan create_plan_template create_task delete_appearance_profile
        delete_memory_entry delete_plan delete_plan_template detect_coding_agent_cli
        discord_debug discord_delete_bot discord_list_bots discord_list_channels
        discord_list_members discord_open_channel discord_open_portal discord_save_bot
        discord_send discord_status ducky_health_snapshot ducky_self_care_run
        gateway_account_status gateway_account_usage get_appearance get_chat_account
        get_chat_context_memory get_ducky_usage get_errors get_gateway_usage get_key_status
        get_log get_log_counts get_memory_entry get_memory_settings get_models
        get_models_catalog get_plan get_plan_template get_provider_usage get_settings
        has_any_api_key import_sound_file instantiate_plan_template list_coding_agents
        list_gateway_accounts list_memory_entries list_plan_templates list_plans list_tasks
        load_appearance_profile login_gateway_account logout_gateway_account memory_report
        plan_add_node plan_delete_node plan_move_node plan_update_node pull_editor_log
        push_appearance_live remove_gateway_account rename_appearance_profile
        rename_gateway_account reorder_gateway_accounts sandbox_apps sandbox_call
        sandbox_delete sandbox_open_folder sandbox_relaunch sandbox_status
        sandbox_stop_program save_agent_settings save_appearance save_appearance_profile
        save_memory_entry save_plan_as_template set_chat_account
        set_conversation_coding_agent set_conversation_thinking_effort set_gateway_account
        set_gateway_account_key set_gateway_auto_switch set_memory_settings set_model
        submit_feedback team_keeper_status test_key update_active_appearance_profile
        update_plan update_plan_template usage_track verify_task write_task_artifact
    """,
    "PanelApiAutomationsMixin": """
        add_workflow_folder approve_workflow_node_code check_workflow_expression
        check_workflow_node_code clear_workflow_runs consume_dropped_file_paths copy_workflow
        copy_workflow_folder delete_workflow delete_workflow_template dismiss_workflow_run
        edit_workflow_node_code emit_workflow_trigger export_workflow_folder
        get_generated_image_attachment get_workflow get_workflow_node_code
        get_workflow_tools_catalog get_workflow_version import_local_workflows
        import_workflow_bundle keep_workflow_preview list_generated_images list_workflow_nodes
        list_workflow_templates list_workflow_versions list_workflows move_workflow_folder
        pick_workflow_files pick_workflow_folder queue_file_pick run_workflow run_workflow_node
        save_workflow save_workflow_template set_workflow_editor_prefs set_workflow_folder
        set_workflow_run_here stop_workflow test_workflow_node use_workflow_template
        workflow_code_api workflow_editor_prefs workflow_open_web workflow_owners
        workflow_run_snapshot workflow_sync
    """,
    "PanelApiVideoMixin": """
        get_ffmpeg_status get_video_settings install_ffmpeg remove_ffmpeg
        set_video_settings stage_video_attachment
    """,
    "PanelApiVoiceLaneMixin": "save_voice_lane_settings voice_lane_settings voice_lane_turn",
    "PanelApiPlanProgressMixin": "plan_progress",
    "PanelApiSwarmMixin": "plan_launch plan_set_planned_assignee swarm_preview swarm_suggest",
}

# Window work the launcher does itself (its own window, drag, dialogs): handler = launcher op.
LAUNCHER_HANDLERS: dict[str, str] = {
    "get_window_bounds": "window.bounds",
    "set_window_bounds": "window.set_bounds",
    "set_sidebar_only_layout": "window.set_bounds",
    "begin_native_window_move": "window.drag",
    "begin_native_window_resize": "window.resize",
    "is_window_maximized": "window.is_maximized",
    "toggle_maximize": "window.maximize",
    "minimize_window": "window.minimize",
    "hide_window": "window.hide",
    "close_this_window": "window.close",
    "exit_all": "window.quit",
    "uses_native_window_chrome": "window.chrome",
    "open_devtools": "window.devtools",
    "pick_project_path": "dialog.folder",
    "pick_workflow_files": "dialog.open_files",
    "pick_workflow_folder": "dialog.folder",
}

# Not in the lean cut: the Python window binds these itself; the page never calls them.
NOT_LEAN_CUT: frozenset[str] = frozenset(
    """
    bind_tk_root bind_window
    """.split()
)


def _build_inventory() -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for cls, names in _JS_API.items():
        for name in names.split():
            if name in NOT_LEAN_CUT:
                entry = {"kind": "js_api", "lean_cut": False, "handler": None, "cls": cls}
            elif name in LAUNCHER_HANDLERS:
                entry = {"kind": "js_api", "lean_cut": True, "handler": LAUNCHER_HANDLERS[name], "cls": cls}
            else:
                entry = {"kind": "js_api", "lean_cut": True, "handler": "python", "cls": cls}
            out[name] = entry
    return out


# {name: {kind: 'js_api', lean_cut: bool, handler: 'python' | 'window.*' | 'dialog.*' | None, cls}}
INVENTORY: dict[str, dict[str, object]] = _build_inventory()


# --- pywebview window, dialog, event and tray calls ---------------------------------
WINDOW_SOURCES: tuple[str, ...] = (
    "frontend/ui_web/win_frameless.py",
    "frontend/ui_web/webview_native_menu.py",
    "frontend/ui_web/window_layout.py",
    "frontend/ui_web/window_bounds.py",
    "frontend/tray_icon.py",
    "frontend/notifications.py",
)
# Names a pywebview window object goes by in those files.
_WINDOW_ROOTS = frozenset({"window", "win", "w", "main_window", "self._window", "api._window"})
_TRAY_ROOTS = frozenset({"pystray", "icon", "tray", "self._icon", "_icon"})


def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _classify(dotted: str) -> tuple[str, str] | None:
    """(kind, normalized name) for a call the launcher must answer for, else None."""
    parts = dotted.split(".")
    if parts[-1] == "create_file_dialog":
        return "dialog", "window.create_file_dialog"
    if "events" in parts[:-1]:
        i = parts.index("events")
        return "event", "window.events." + ".".join(parts[i + 1 :])
    if parts[0] == "webview":
        return "webview", dotted
    for n in (2, 1):
        root = ".".join(parts[:n])
        if len(parts) > n and root in _WINDOW_ROOTS:
            return "window", "window." + ".".join(parts[n:])
        if len(parts) > n and root in _TRAY_ROOTS:
            return "tray", "tray." + ".".join(parts[n:])
    return None


def scan_window_calls(sources: tuple[str, ...] = WINDOW_SOURCES) -> dict[str, dict[str, object]]:
    """{normalized call: {kind, files}} for every pywebview/tray call in ``sources``."""
    found: dict[str, dict[str, object]] = {}
    for rel in sources:
        tree = ast.parse((DUCKY_APP / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                dotted = _dotted(node.func)
            elif isinstance(node, ast.AugAssign):  # window.events.moved += handler
                dotted = _dotted(node.target)
            else:
                continue
            hit = _classify(dotted) if dotted else None
            if hit is None:
                continue
            kind, name = hit
            entry = found.setdefault(name, {"kind": kind, "files": []})
            if rel not in entry["files"]:
                entry["files"].append(rel)
    return found


# Every call scan_window_calls finds, and who answers it with the launcher on.
# handler: the launcher op that replaces it; None = stays on the python (pywebview) path.
WINDOW_INVENTORY: dict[str, dict[str, object]] = {
    "window.destroy": {"kind": "window", "lean_cut": True, "handler": "window.close"},
    "window.maximize": {"kind": "window", "lean_cut": True, "handler": "window.maximize"},
    "window.minimize": {"kind": "window", "lean_cut": True, "handler": "window.minimize"},
    "window.restore": {"kind": "window", "lean_cut": True, "handler": "window.restore"},
    "window.events.moved": {"kind": "event", "lean_cut": True, "handler": "window.bounds"},
    "window.events.resized": {"kind": "event", "lean_cut": True, "handler": "window.bounds"},
    "window.events.closing": {"kind": "event", "lean_cut": True, "handler": "window.bounds"},
    # The tray stays in Python (pystray runs its own thread and message loop).
    "tray.Icon": {"kind": "tray", "lean_cut": True, "handler": "python"},
    "tray.Menu": {"kind": "tray", "lean_cut": True, "handler": "python"},
    "tray.MenuItem": {"kind": "tray", "lean_cut": True, "handler": "python"},
    "tray.stop": {"kind": "tray", "lean_cut": True, "handler": "python"},
}


def export_json() -> str:
    """Both inventories as stable JSON (sorted keys), for the Rust side and for diffs."""
    import json

    return json.dumps(
        {"js_api": INVENTORY, "window": WINDOW_INVENTORY},
        sort_keys=True,
        indent=1,
        ensure_ascii=False,
    )


def handled_by_launcher(name: str) -> bool:
    handler = (INVENTORY.get(name) or {}).get("handler")
    return isinstance(handler, str) and handler.split(".", 1)[0] in ("window", "dialog")
