"""Host-side panel meta tools for the embedded agent."""

from __future__ import annotations

_DELEGATION_TOOLS = frozenset(
    {
        "ducky_list_duckies",
        "ducky_spawn_chat",
        "ducky_send_chat_message",
        "ducky_recycle_subagent",
        "ducky_recycle_member",
        "ducky_fresh_session",
        "ducky_read_chat",
        "ducky_group_create",
        "ducky_group_invite",
        "ducky_set_muted",
        "ducky_group_stop",
        "ducky_group_members",
        "ducky_group_set_leader",
        "ducky_group_add_member",
        "ducky_agent_list",
        "ducky_agent_send",
        "ducky_agent_inbox",
    }
)

_STORE_BROWSE = frozenset(
    {
        "ducky_store_categories",
        "ducky_store_search",
        "ducky_store_get",
    }
)

_STORE_MUTATE = frozenset(
    {
        "ducky_store_install",
        "ducky_store_update",
        "ducky_store_set_enabled",
        "ducky_store_remove",
    }
)

# Cursor-style floor: meta discover/call + status/ask. Everything else via ducky_call_tool.
CORE_TOOLS = frozenset(
    {
        "ducky_get_status",
        "ducky_get_tools",
        "ducky_call_tool",
        "ducky_find",
        "skill_search",
        "ducky_ask_user",
        "ducky_rename_self",
        "web_search",
        "web_fetch",
    }
)

EXTENDED_TOOLS = frozenset(
    {
        # web_fetch on a video link already reads it; frames and GitHub go via ducky_call_tool.
        "web_video",
        "web_github",
        "sandbox_open",
        "sandbox_fetch",
        "sandbox_clone",
        "sandbox_worktree",
        "sandbox_setup",
        "sandbox_run",
        "sandbox_launch",
        "sandbox_look",
        "sandbox_http",
        "sandbox_mcp",
        "sandbox_stop",
        "sandbox_clear",
        "ducky_health",
        "ducky_self_care",
        "ducky_generate_image",
        "ducky_get_local_project",
        "ducky_list_projects",
        "ducky_set_project",
        "ducky_launch_uefn",
        "ducky_sync_project_to_uefn",
        "ducky_get_errors",
        "ducky_agent_transcript",
        "ducky_agent_stop",
        "ducky_list_chats",
        "ducky_list_archive",
        "ducky_create_chat",
        "ducky_create_folder",
        "ducky_rename_chat",
        "ducky_move_chat",
        "ducky_append_chat_message",
        "ducky_get_chat_context",
        "ducky_reset_chat_context",
        "ducky_restore_chat_context",
        "ducky_list_tasks",
        "ducky_create_task",
        "ducky_add_task_phase",
        "ducky_task_handoff",
        "ducky_verify_task",
        "ducky_create_plan",
        "ducky_update_plan",
        "ducky_get_plan",
        "ducky_list_plans",
        "ducky_plan_add_node",
        "ducky_plan_update_node",
        "ducky_plan_delete_node",
        "ducky_plan_move_node",
        "ducky_list_plan_templates",
        "ducky_get_plan_template",
        "ducky_create_plan_template",
        "ducky_update_plan_template",
        "ducky_instantiate_plan_template",
        "ducky_settings_schema",
        "ducky_settings_get",
        "ducky_skills_list_packs",
        "ducky_mcp_list_plugins",
        "ducky_profile_list",
        "ducky_profile_get",
        "ducky_plugin_list",
        "ducky_plugin_read_file",
        "ducky_plugin_reference",
        "ducky_plugin_errors",
        "ducky_verse_template_list",
        "ducky_verse_template_get",
        # Old alias of ducky_get_tools(pattern=): still callable, no longer in the floor.
        "ducky_find_tools",
    }
) | _DELEGATION_TOOLS | _STORE_BROWSE | _STORE_MUTATE

TOOLS = CORE_TOOLS | EXTENDED_TOOLS

PLAN_TOOLS = frozenset(
    {
        "ducky_get_status",
        "ducky_get_tools",
        "ducky_find",
        "ducky_call_tool",
        "ducky_find_tools",
        "skill_search",
        "ducky_ask_user",
        "ducky_rename_self",
        "web_search",
        "web_fetch",
        "web_video",
        "web_github",
        "ducky_create_plan",
        "ducky_update_plan",
        "ducky_get_plan",
        "ducky_plan_add_node",
        "ducky_plan_update_node",
        "ducky_plan_delete_node",
        "ducky_plan_move_node",
    }
)
