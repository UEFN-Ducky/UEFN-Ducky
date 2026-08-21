"""Plan-mode tool allow predicate: discover/outline only, no mutators."""

from __future__ import annotations

import re

from backend.agent.toolsets.categories import PLAN_TOOLS

# Leaf-name stems that look like read/discover (after nested ``prefix__``).
_ALLOW_SUBSTRINGS: tuple[str, ...] = (
    "_status",
    "_balance",
    "_capabilities",
    "_list_",
    "_search",
    "_get_",
    "_discover_",
    "_info",
    "_preview",
    "_categories",
    "_poll_",
    "_schema",
    "_overview",
    "_snapshot",
    "_audit",
    "_results",
    "_digest",
    "_bounds",
    "_distance",
    "_stats",
    "_state",
)

_ALLOW_PREFIXES: tuple[str, ...] = (
    "list_",
    "get_",
    "search_",
    "read_",
    "inspect_",
    "find_",
    "does_",
    "measure_",
    "check_",
    "describe_",
    "validate_",
    "session_status",
)

# Mutating / spend / side-effect stems — block even if another allow token matches.
_BLOCK_SUBSTRINGS: tuple[str, ...] = (
    "_send_",
    "_create_",
    "_set_",
    "_delete_",
    "_remove_",
    "_install_",
    "_execute_",
    "_generate_",
    "_download_",
    "_download",  # suffix form: meshy_discover_download
    "_import_",
    "_import",
    "_text_to_3d_preview",  # spends credits; Agent-only
    "_text_to_3d",
    "_text_to_image",
    "_image_to_3d",
    "_image_to_image",
    "_multi_image",
    "_update_",
    "_write_",
    "_ban_",
    "_kick_",
    "_timeout_",
    "_animate",
    # "_rig" alone matches get_ik_rig_info — only block leaf ending in _rig
    "_remesh",
    "_retexture",
    "_convert",
    "_resize",
    "_connect",
    "_disconnect",
    "_clear_",
    "_scatter",
    "_sculpt",
    "_rename",
    "_duplicate",
    "_spawn",
    "_destroy",
    "_apply",
    "_fill_",
    "_patch_",
    "_wire_",
    "_save",
    "_compile",
    "_push_",
    "_run_",
    "_simulate",
    "_play_",
    "_stop_",
    "_add_",
    "_assign_",
    "_control_",
    "_layout_",
    "_recompile",
    "_retarget",
    "_map_",
    "_snap_",
    "_align_",
    "_distribute_",
    "_terrain_generate",
    "_landscape_create",
    "_landscape_sculpt",
    "_pcg_generate",
    "_select_",
    "_focus_",
    "_attach_",
    "_open_",
    "_export_",
    "_fixup_",
    "_navigate",
    "_command",
    "_batch",
    "_cache_clear",
    "_vim_set",
    "_vim_command",
)

_BLOCK_PREFIXES: tuple[str, ...] = (
    "create_",
    "set_",
    "delete_",
    "remove_",
    "spawn_",
    "send_",
    "write_",
    "install_",
    "update_",
    "duplicate_",
    "rename_",
    "import_",
    "export_",
    "fill_",
    "wire_",
    "patch_",
    "save_",
    "play_",
    "stop_",
    "run_",
    "simulate_",
    "control_",
    "assign_",
    "connect_",
    "disconnect_",
    "clear_",
    "add_",
    "align_",
    "snap_",
    "distribute_",
    "retarget_",
    "auto_map_",
    "convert_",
    "instantiate_",
    "destroy_",
    "select_",
    "focus_",
    "attach_",
    "open_",
    "navigate",
    "exec_",
    "ai_generate_",
    "translate_ui_batch",
    "translate_ui_cache_clear",
    "workspace_editor_vim_",
    "workspace_write_",
    "workspace_compile_",
    "workspace_push_",
    "workspace_open_",
    "gdrive_connect",
    "gdrive_disconnect",
    "gdrive_download",
    "gdrive_import_",
    "discord_send_",
    "discord_create_",
    "discord_edit_",
    "discord_delete_",
    "discord_set_",
    "discord_timeout_",
    "discord_kick_",
    "discord_ban_",
)

_ALLOW_EXACT: frozenset[str] = frozenset(
    {
        "ping",
        "session_status",
        "device_graph_snapshot",
        "device_graph_audit",
        "actor_state_snapshot",
        "actor_state_diff",
        "uefn_skill",
        "skill_read_subskill",
        "uefn_editor_python_hints",
        "code_detect_project",
        "code_list_errors",
        "workspace_list_dir",
        "workspace_read_file",
        "workspace_list_verse_errors",
        "workspace_editor_get_state",
        "gdrive_status",
        "gdrive_list",
        "gdrive_search",
        "gdrive_file_info",
        "browser_status",
        "browser_cdp_info",
        "browser_runtime_info",
        "browser_site_security",
        "blender_status",
        "meshy_balance",
        "meshy_status",
        "studio3d_balance",
        "studio3d_status",
        "translate_ui_cache_get",
        "ik_retarget_capabilities",
        "anim_author_capabilities",
        "worldgen_capabilities",
        "scene_graph_capabilities",
        "data_table_capabilities",
        "niagara_capabilities",
        "metahuman_capabilities",
        "metahuman_can_assemble",
        "metahuman_list",
        "ducktactoe_state",
        "landscape_list",
        "verse_template_list",
    }
)

_BLOCK_EXACT: frozenset[str] = frozenset(
    {
        "reload_listener",  # mild side effect; keep Agent unless already in PLAN_TOOLS
        "shutdown",
        "take_high_res_screenshot",
        "exec_console_command",
        "save_all_dirty",
        "save_current_level",
        "blender_execute_blender_code",
        "blender_redeploy_addon",
        "browser_navigate",
        "browser_command",
        "browser_clear_data",
        "ducktactoe_new_game",
        "ducktactoe_move",
        "verse_test_scaffold",
        "verse_test_run",
        "verse_test_add_case",
        "tester_create_simulation",
        "tester_run_simulation",
        "simulate_device_event",
        "metahuman_assemble_uefn",
        "verse_template_apply",
        "meshy_discover_download",
        "meshy_text_to_3d_preview",
        "meshy_rig",
    }
)


def tool_leaf_name(name: str) -> str:
    """Nested MCP ``prefix__tool`` → ``tool``; otherwise the full name."""
    if "__" in name:
        return name.rsplit("__", 1)[-1]
    return name


def _plugin_declared_plan_tools() -> frozenset[str]:
    out: set[str] = set()
    try:
        from backend.uefn_plugins.host import plugin_plan_tools

        out.update(plugin_plan_tools())
    except Exception:
        pass
    try:
        from backend.agent.toolsets.mcp_plugins import nested_mcp_plan_tool_names

        out.update(nested_mcp_plan_tool_names())
    except Exception:
        pass
    return frozenset(out)


def _is_destructive_name(name: str) -> bool:
    try:
        from backend.agent.toolsets import is_destructive

        return is_destructive(name)
    except Exception:
        return False


def is_plan_safe_tool(name: str) -> bool:
    """True when Plan mode may expose this tool (discover/outline, not mutate)."""
    n = (name or "").strip()
    if not n:
        return False
    if n in PLAN_TOOLS:
        return True
    if n in _plugin_declared_plan_tools():
        return True
    if _is_destructive_name(n):
        return False
    if n in _BLOCK_EXACT:
        return False
    if n in _ALLOW_EXACT:
        return True

    leaf = tool_leaf_name(n)
    if leaf in _BLOCK_EXACT:
        return False
    if leaf in _ALLOW_EXACT:
        return True

    for pref in _BLOCK_PREFIXES:
        if leaf.startswith(pref) or n.startswith(pref):
            return False

    # Read-shaped prefixes win over broad mutator substrings (e.g. get_ik_rig_info
    # vs "_rig", find_clear_area vs "_clear_", get_retarget_preset vs "_retarget").
    for pref in _ALLOW_PREFIXES:
        if leaf.startswith(pref):
            return True
    if leaf.endswith("_list") or leaf.endswith("_info") or leaf.endswith("_status"):
        return True
    if leaf.endswith("_rig"):
        return False

    for sub in _BLOCK_SUBSTRINGS:
        if sub in leaf or sub in n:
            return False

    for sub in _ALLOW_SUBSTRINGS:
        if sub in leaf:
            return True

    # discord_list_* / discord_read_* without needing underscore after list
    if re.match(r"^discord_(list|read)_", leaf):
        return True
    if re.match(r"^(list|get|search|read|inspect|find|validate)_", leaf):
        return True

    return False
