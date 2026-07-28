"""Optional tool bundles unlocked by keywords in the user message."""

from __future__ import annotations

import json
import re
from typing import Any

from backend.agent.toolsets.workflows import agent_tool_name

_SCENE_TOOLS = frozenset(
    {
        "get_all_actors",
        "get_selected_actors",
        "get_actor_properties",
        "set_actor_properties",
        "spawn_actor",
        "set_actor_transform",
        "select_actors",
        "focus_selected",
        "delete_actors",
    }
)

_ASSET_TOOLS = frozenset(
    {
        "list_assets",
        "search_assets",
        "get_asset_info",
        "get_selected_assets",
        "does_asset_exist",
        "save_asset",
        "duplicate_asset",
        "rename_asset",
        "delete_asset",
        "open_asset_in_uefn",
        "validate_uefn_asset",
        "uefn_editor_python_hints",
    }
)

_MATERIAL_TOOLS = frozenset(
    {
        "create_material",
        "create_material_instance",
        "duplicate_material",
        "connect_material_nodes",
        "disconnect_material_nodes",
        "connect_material_output",
        "add_material_expression",
        "delete_material_expression",
        "clear_material_expressions",
        "set_material_expression_property",
        "get_material_expression_info",
        "get_material_info",
        "set_material_flags",
        "layout_material_expressions",
        "assign_material_to_mesh",
        "list_material_expressions",
        "list_uefn_material_expression_classes",
        "set_material_instance_scalar",
        "set_material_instance_vector",
        "set_material_instance_texture",
        "recompile_material",
    }
)

_NIAGARA_TOOLS = frozenset(
    {
        "niagara_capabilities",
        "list_niagara_systems",
        "get_niagara_system_info",
        "create_niagara_system",
        "get_niagara_component_info",
        "set_niagara_component_parameter",
        "control_niagara_actor",
        # publish/cook diagnosis + compliant replacement fallback
        "validate_uefn_asset",
        "get_dependencies",
        "get_referencers",
        "duplicate_asset",
        "delete_asset",
        "save_asset",
        "rename_asset",
        "does_asset_exist",
        "open_asset_in_uefn",
        "spawn_actor",
        "take_high_res_screenshot",
        "save_current_level",
        "fixup_redirectors",
        "save_directory",
    }
)

_DATA_TABLE_TOOLS = frozenset(
    {
        "data_table_capabilities",
        "list_data_tables",
        "get_data_table_info",
        "get_data_table_rows",
        "fill_data_table_from_json",
        "fill_data_table_from_csv",
        "create_data_table",
    }
)

_PIPELINE_TOOLS = frozenset(
    {
        "create_folder",
        "import_asset",
        "export_asset",
        "get_dependencies",
        "get_referencers",
        "validate_uefn_asset",
        "fixup_redirectors",
        "save_directory",
    }
)

_PCG_TOOLS = frozenset({"pcg_generate", "pcg_get_graph_info"})

_WORLDGEN_TOOLS = frozenset(
    {
        "worldgen_capabilities",
        "terrain_generate",
        "terrain_get_info",
        "terrain_remove_generated",
        "foliage_list_sources",
        "foliage_scatter",
        "foliage_get_stats",
        "foliage_clear_generated",
        "landscape_list",
        "landscape_get_info",
        "landscape_create",
        "landscape_rename",
        "landscape_sculpt",
        "validate_uefn_asset",
        "get_ground_z",
        "check_area_clear",
        "find_clear_area",
        "get_level_bounds",
        "set_viewport_camera",
        "take_high_res_screenshot",
        "save_current_level",
        "search_assets",
        "does_asset_exist",
    }
)

_SCENE_GRAPH_TOOLS = frozenset(
    {
        "scene_graph_capabilities",
        "list_entities",
        "get_entity_info",
        "list_scene_component_classes",
        "get_selected_entities",
        "select_entities",
        "create_entity",
        "set_entity_transform",
        "add_entity_component",
        "remove_entity_component",
        "set_entity_component_property",
        "get_entity_component_property",
        "rename_entity",
        "set_entity_parent",
        "duplicate_entity",
        "destroy_entity",
        "create_prefab_from_entities",
        "instantiate_prefab",
        "convert_actors_to_entities",
        # writing Verse components/prefab spawns needs digest ground truth
        "get_verse_api",
        "search_verse_digest",
    }
)

_DIGEST_TOOLS = frozenset(
    {
        "search_verse_digest",
        "get_verse_api",
        "list_verse_digests",
        "list_verse_modules",
        "list_verse_types",
        "list_verse_devices",
    }
)

_LEVEL_DESIGN_TOOLS = frozenset(
    {
        # spatial awareness + precise placement
        "get_actor_bounds",
        "get_level_bounds",
        "measure_distance",
        "check_area_clear",
        "find_clear_area",
        "get_ground_z",
        "snap_actor_to_ground",
        "snap_actor_to_grid",
        "align_actors",
        "distribute_actors",
        # level organization/query primitives that had no bundle before
        "duplicate_actor",
        "get_actors_in_radius",
        "attach_actor",
        "set_actor_folder",
        "set_actor_tags",
        "get_actor_bone_transform",
        "list_actor_components",
        # large greybox / blockout
        "execute_python",
        "uefn_editor_python_hints",
        "create_material",
        "assign_material_to_mesh",
        "set_viewport_camera",
        "take_high_res_screenshot",
        "save_current_level",
        "spawn_actor",
        "search_assets",
        "does_asset_exist",
        # per-area landscapes + presets
        "area_list",
        "area_create",
        "blockout_list_presets",
        "blockout_layout",
        "terrain_generate",
        "terrain_get_info",
    }
)

_TESTING_TOOLS = frozenset(
    {
        "device_graph_snapshot",
        "device_graph_audit",
        "simulate_device_event",
        "tester_list_devices",
        "tester_list_tests",
        "tester_create_simulation",
        "tester_run_simulation",
        "verse_test_add_case",
        "verse_test_scaffold",
        "verse_test_run",
        "verse_test_results",
        "tester_get_results",
        "session_status",
        "actor_state_snapshot",
        "actor_state_diff",
        "play_in_editor",
        "stop_pie",
        "get_editor_log",
        "workspace_compile_verse",
        "workspace_push_verse_changes",
        "workspace_list_verse_errors",
        "workspace_read_file",
        "workspace_write_file",
        "find_devices",
        "inspect_verse_device",
        "inspect_creative_device",
        "take_high_res_screenshot",
    }
)

_ANIMATION_TOOLS = frozenset(
    {
        # retargeting (READ/CREATE/CHANGE/BAKE primitives)
        "ik_retarget_capabilities",
        "list_skeleton_bones",
        "get_retarget_preset",
        "get_ik_rig_info",
        "get_ik_retargeter_info",
        "create_ik_rig_asset",
        "create_ik_retargeter_asset",
        "set_retarget_root",
        "add_retarget_chains",
        "remove_retarget_chains",
        "auto_map_retarget_chains",
        "retarget_animation",
        "retarget_animation_pipeline",
        # items/props on skeletons (sockets + attach + verify)
        "get_skeletal_mesh_info",
        "list_skeleton_sockets",
        "add_skeleton_socket",
        "remove_skeleton_socket",
        "get_actor_bone_transform",
        "list_actor_components",
        "attach_actor",
        # find the item mesh, place it, persist the result
        "search_assets",
        "spawn_actor",
        "save_asset",
    }
)

_VERSE_EXTENDED_TOOLS = frozenset(
    {
        "set_currency_config_entries",
        "resize_verse_array",
        "patch_verse_array_entry",
        "wire_verse_device_array",
        "wire_verse_prop_assets",
        "set_verse_texture_icon",
        "list_verse_reference_types",
        "list_verse_property_hashes",
        "workspace_push_verse_changes",
        "search_assets",
        "list_assets",
        "does_asset_exist",
        "spawn_actor",
        "set_actor_transform",
        "get_viewport_camera",
        "set_actor_label",
        "save_current_level",
        # digest ground truth for writing correct Verse
        "search_verse_digest",
        "get_verse_api",
        "list_verse_digests",
        "list_verse_modules",
        "list_verse_types",
        "list_verse_devices",
        # Custom AppData Verse scaffolds (not Store verse_template_*).
        "ducky_verse_template_list",
        "ducky_verse_template_get",
        "ducky_verse_template_save",
        "ducky_verse_template_delete",
        "ducky_verse_template_apply",
    }
)

# Shared noun tail for device/trigger placement prompts ("place 5 triggers", etc.).
_DEVICE_NOUN = r"triggers?|devices?|granters?|spawners?|buttons?"

_PANEL_CHAT_TOOLS = frozenset(
    {
        "ducky_list_duckies",
        "ducky_list_chats",
        "ducky_read_chat",
        "ducky_create_chat",
        "ducky_create_folder",
        "ducky_rename_chat",
        "ducky_move_chat",
        "ducky_send_chat_message",
        "ducky_spawn_chat",
        "ducky_group_create",
        "ducky_group_invite",
        "ducky_group_members",
        "ducky_group_set_leader",
        "ducky_group_add_member",
        "ducky_recycle_member",
        "ducky_recycle_subagent",
        "ducky_agent_list",
        "ducky_agent_send",
        "ducky_agent_inbox",
        "ducky_agent_transcript",
        "ducky_agent_stop",
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
    }
)

_WORKSPACE_EDITOR_TOOLS = frozenset(
    {
        "workspace_open_verse_file",
        "code_open_file",
    }
)

# Panel-pathway tools: settings/plugins/skills/profiles/language + walkthrough UI.
# Unlocked when the user talks about configuring the app or asks "how do I…".
_PANEL_UI_TOOLS = frozenset(
    {
        "ducky_ui_navigate",
        "ducky_ui_list_targets",
        "ducky_walkthrough_run",
        "ducky_ask_user",
        "ducky_settings_schema",
        "ducky_settings_get",
        "ducky_settings_set",
        "ducky_mcp_list_plugins",
        "ducky_mcp_set_plugin",
        "ducky_mcp_upsert_server",
        "ducky_mcp_remove_server",
        "ducky_mcp_apply",
        "ducky_mcp_validate",
        "ducky_skills_list_packs",
        "ducky_skills_set_pack",
        "ducky_skills_open_in_studio",
        "ducky_skills_create_pack",
        "ducky_skills_write_subskill",
        "ducky_profile_list",
        "ducky_profile_get",
        "ducky_profile_set",
        "ducky_profile_create",
        "ducky_profile_duplicate",
        "ducky_i18n_list_languages",
        "ducky_i18n_set_language",
        # Store mutate (browse is CORE). Intent when user asks to install/enable/remove.
        "ducky_store_install",
        "ducky_store_update",
        "ducky_store_set_enabled",
        "ducky_store_remove",
        # AI-made desktop plugins (shared AppData drafts → install source=ai).
        "ducky_plugin_scaffold",
        "ducky_plugin_list",
        "ducky_plugin_write_file",
        "ducky_plugin_read_file",
        "ducky_plugin_validate",
        "ducky_plugin_install",
        "ducky_plugin_delete_draft",
        "ducky_plugin_reference",
        # Custom Verse templates (AppData) — never Store plugin verse.templates.
        "ducky_verse_template_list",
        "ducky_verse_template_get",
        "ducky_verse_template_save",
        "ducky_verse_template_delete",
        "ducky_verse_template_apply",
    }
)

# Discord tools live on the Discord UEFN plugin (agent.tools contribution).

# Gate for the ~3k-token operator-skill system block: only chats doing verse/device
# work pay for it. Status/Q&A chats rely on the `uefn_skill` tool instead.
SKILL_INTENT_RE = re.compile(
    r"\b(create|write|edit|add|make|build|implement|fix|compile|verse|devices?|granters?"
    r"|triggers?|wiring|wire|@editable|spawn|place|put|wallet|currency|file|files|directly|code|logic"
    r"|npc|grant|walk|\.verse)\b",
    re.I,
)


def skill_intent_matched(user_text: str, history: list[dict] | None = None) -> bool:
    """True when the current or any prior USER message looks like verse/device work."""
    if SKILL_INTENT_RE.search(user_text or ""):
        return True
    for message in history or []:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and SKILL_INTENT_RE.search(content):
            return True
    return False


def _parse_tool_result_data(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict) and "data" in parsed:
        inner = parsed.get("data")
        if isinstance(inner, str):
            try:
                inner_parsed = json.loads(inner)
                if isinstance(inner_parsed, dict):
                    return inner_parsed
            except json.JSONDecodeError:
                pass
        if isinstance(inner, dict):
            return inner
    return parsed if isinstance(parsed, dict) else None


def tools_suggested_by_history(history: list[dict[str, Any]] | None) -> set[str]:
    """Unlock tools hinted by the last successful inspect / wiring tool results."""
    suggested: set[str] = set()
    for message in reversed(history or []):
        if message.get("role") != "assistant":
            continue
        for block in reversed(message.get("blocks") or []):
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_call" or block.get("status") != "success":
                continue
            result = block.get("result") or {}
            if not result.get("ok"):
                continue
            data = _parse_tool_result_data(result.get("data"))
            if not data:
                continue
            for name in data.get("allowed_next_tools") or []:
                mapped = agent_tool_name(str(name))
                if mapped:
                    suggested.add(mapped)
            if suggested:
                return suggested
    return suggested


# Extra tools beyond CORE_TOOLS when the message matches (reduces token load on unrelated turns).
INTENT_KEYWORDS: list[tuple[re.Pattern[str], frozenset[str]]] = [
    (
        re.compile(
            r"\b(create|write|edit|add|make|build|implement|fix|compile|verse|devices?|granters?"
            r"|triggers?|wiring|@editable|spawn|place|put|file|files|\.verse|npc|grant)\b",
            re.I,
        ),
        _VERSE_EXTENDED_TOOLS,
    ),
    (
        re.compile(
            rf"\b(place|put|add|drop|layout)\b.*\b({_DEVICE_NOUN})\b|"
            rf"\b({_DEVICE_NOUN})\b.*\b(in the level|in level|into the level|into level|on the map)\b",
            re.I,
        ),
        _VERSE_EXTENDED_TOOLS,
    ),
    (
        re.compile(r"\b(actor|transform|mesh|prop|delete\s+actor|select\s+actor|viewport|place|put)\b", re.I),
        _SCENE_TOOLS,
    ),
    (
        re.compile(r"\b(asset|material|import|texture|shader|mi_|rename_asset|duplicate)\b", re.I),
        _ASSET_TOOLS,
    ),
    (
        re.compile(
            r"\b(subagent|sub-agent|spawn\s+chat|other\s+chat|multi.?chat|ducky_.*chat"
            r"|delegate|delegat|hand\s*off|duckies|which\s+ducky|another\s+ducky"
            r"|programmer|designer|specialist|coding\s+agent|claude\s+code|codex|byoa"
            r"|task|tasks|epic|phase|verify|handoff|artifact"
            r"|plan|plans|planning|roadmap)\b",
            re.I,
        ),
        _PANEL_CHAT_TOOLS,
    ),
    (
        re.compile(
            r"\b(do\s+it|go\s+ahead|proceed|start\s+now|get\s+started|just\s+do"
            r"|make\s+it|build\s+it|go\s+build|ship\s+it|execute)\b",
            re.I,
        ),
        _PANEL_CHAT_TOOLS,
    ),
    (
        # Memory read/capture/overview tools are CORE (always available); only the
        # destructive delete needs an intent to appear.
        re.compile(
            r"\b(forget|delete\s+memory|remove\s+memory|prune\s+memory|memory\s+entry)\b",
            re.I,
        ),
        frozenset({"project_memory_delete"}),
    ),
    (
        re.compile(r"\b(editor\s+state|open_verse_file)\b", re.I),
        _WORKSPACE_EDITOR_TOOLS,
    ),
    (
        re.compile(
            r"\b(typescript|javascript|\.tsx?|\.jsx?|eslint|tsc|rust|cargo|php|clang|c\+\+|cpp"
            r"|code_list_errors|code\s*errors?)\b",
            re.I,
        ),
        frozenset({"code_list_errors", "code_detect_project", "code_open_file"}),
    ),
    (
        re.compile(
            r"\b(settings?|preferences?|configure|config|navigate|spotlight|highlight|show\s+me\s+where"
            r"|walk\s+me\s+through|guide\s+me|walkthrough|tutorial|how\s+do\s+i|show\s+me\s+how"
            r"|mcp\s+plugin|mcp\s+server|enable\s+.*plugin|disable\s+.*plugin"
            r"|apply\s+to\s+(ide|cursor|claude)|skill\s+pack|appearance|theme|persona|ducky\s+style"
            r"|language|translat|locale|i18n"
            r"|store|marketplace|install\s+(plugin|skill|pack)|uninstall|sideload"
            r"|ai[- ]?made\s+plugin|ai\s+plugin|desktop\s+plugin|create\s+(a\s+)?plugin"
            r"|build\s+(a\s+)?plugin|write\s+(a\s+)?plugin|author\s+(a\s+)?plugin"
            r"|customi[sz]e\s+(the\s+)?(app|ducky)|skin|chrome|plugin\s+draft"
            r"|ducky_plugin_|ai_plugins"
            r"|verse\s*template|custom\s+template|system\s+template"
            r"|ducky_verse_template_)\b",
            re.I,
        ),
        _PANEL_UI_TOOLS,
    ),
    (
        re.compile(r"\b(level\s+info|get_level)\b", re.I),
        frozenset({"get_level_info"}),
    ),
    (
        re.compile(r"\b(material|shader|mi_|material instance)\b", re.I),
        _MATERIAL_TOOLS,
    ),
    (
        re.compile(r"\b(import|export|dependency|referencer|redirector|validate|publish\s+blocker)\b", re.I),
        _PIPELINE_TOOLS,
    ),
    (
        re.compile(r"\b(pcg|procedural)\b", re.I),
        _PCG_TOOLS | _LEVEL_DESIGN_TOOLS | _SCENE_TOOLS | _WORLDGEN_TOOLS,
    ),
    (
        re.compile(
            r"\b(landscape|terrain|heightfield|heightmap|foliage|vegetation|biome|forest|sculpt)\b",
            re.I,
        ),
        _WORLDGEN_TOOLS | _LEVEL_DESIGN_TOOLS | _PCG_TOOLS,
    ),
    (
        re.compile(
            r"\b(layout|align|distribute|arrange|snap|spacing|space\s+between|bounds|overlap(s|ping)?"
            r"|blockout|greybox|graybox|level\s+design|clear\s+(area|space)|room\s+for|fits?\b|ground|floor|evenly"
            r"|row\s+of|grid\s+of|circle\s+of|duplicate|attach|organi[sz]e|city|canal|forest|castle"
            r"|area_create|area_list|hub\b|lobby|arena|store\s*(area|room|zone)?)\b",
            re.I,
        ),
        _LEVEL_DESIGN_TOOLS | _SCENE_TOOLS | _MATERIAL_TOOLS | _WORLDGEN_TOOLS,
    ),
    (
        re.compile(
            r"\b(anim(ation|ations|s)?|retarget(ing|er)?|skeletons?|skeletal|sockets?|bones?"
            r"|ik\s*rigs?|rig(ging)?|attach(ing|ments?)?|equip(ping)?|wear(able|ing)?"
            r"|accessor(y|ies)|hats?|helmets?|backpacks?|holsters?|turbans?"
            r"|(items?|props?|weapons?|meshe?s?)\s+(on|onto|to)\b)\b",
            re.I,
        ),
        _ANIMATION_TOOLS,
    ),
    (
        re.compile(r"\b(niagara|vfx|particles?|emitters?)\b", re.I),
        _NIAGARA_TOOLS,
    ),
    (
        re.compile(r"\b(scene\s*graph|entit(y|ies)|prefabs?|scene\s*component)\b", re.I),
        _SCENE_GRAPH_TOOLS,
    ),
    (
        re.compile(r"\b(digests?|verse\s+api|api\s+signature|which\s+(function|method|class)|module\s+path|using\s*\{)\b", re.I),
        _DIGEST_TOOLS,
    ),
    (
        re.compile(r"\b(data\s?tables?|datatables?|csv)\b", re.I),
        _DATA_TABLE_TOOLS,
    ),
    (
        re.compile(
            r"\b(test|tests|testing|playtest|qa|assert|simulate|simulation|harness"
            r"|device\s+graph|unwired|orphan\s+device|verify\s+(wiring|devices?|leveling|movement)"
            r"|ducky.?test|session\s+status|pie)\b",
            re.I,
        ),
        _TESTING_TOOLS,
    ),
    (
        re.compile(r"\b(camera|viewport|screenshot)\b", re.I),
        frozenset(
            {
                "get_viewport_camera",
                "set_viewport_camera",
            }
        ),
    ),
]

# Intent bundles are legacy hints for docs/tests. Runtime discovery is the tool
# index + ducky_get_tools / ducky_call_tool — EXTENDED tools need not appear here.

