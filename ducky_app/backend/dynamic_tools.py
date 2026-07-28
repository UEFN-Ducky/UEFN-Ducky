"""Auto-expose listener commands that have no static @mcp.tool wrapper.

The host exe is frozen, so its static wrappers lag behind newly added listener
commands until a rebuild. This module runs at bridge start (every Cursor / Claude
connect): it asks the LIVE listener for its command manifest (``describe_commands``)
and registers a thin passthrough tool for anything not already covered — so the
client's tool list is always complete. When the listener is offline it falls back
to the manifest cached from the last successful fetch.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable, Optional

from backend.json_util import tool_json
from backend.server import mcp

# Removed/forbidden commands stay hidden even if an old listener still lists them.
_NEVER_EXPOSE = frozenset(
    {
        "batch_commands",
        "spawn_actor_batch",
        "setup_verse_device",
        "bulk_set_device_settings",
        "bulk_set_verse_editables",
        "bulk_wire_verse_device",
        # Materials plugin owns these on the bridge (api.tool); do not auto-expose.
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
        # Domain Store plugins
        "actor_state_diff",
        "actor_state_snapshot",
        "add_entity_component",
        "add_retarget_chains",
        "add_sequence_binding",
        "add_skeleton_socket",
        "add_transform_keys",
        "add_widget_binding",
        "add_widget_to_tree",
        "ai_generate_python",
        "align_actors",
        "anim_author_capabilities",
        "attach_actor",
        "auto_map_retarget_chains",
        "check_area_clear",
        "control_niagara_actor",
        "convert_actors_to_entities",
        "create_anim_sequence",
        "create_data_table",
        "create_entity",
        "create_folder",
        "create_ik_retargeter_asset",
        "create_ik_rig_asset",
        "create_level_sequence",
        "create_niagara_system",
        "create_prefab_from_entities",
        "create_widget_blueprint",
        "data_table_capabilities",
        "delete_actors",
        "delete_asset",
        "describe_class",
        "destroy_entity",
        "device_graph_audit",
        "device_graph_snapshot",
        "distribute_actors",
        "does_asset_exist",
        "ducky_memory_overview",
        "duplicate_actor",
        "duplicate_asset",
        "duplicate_entity",
        "exec_console_command",
        "export_asset",
        "fill_data_table_from_csv",
        "fill_data_table_from_json",
        "find_clear_area",
        "find_devices",
        "fixup_redirectors",
        "focus_selected",
        "foliage_clear_generated",
        "foliage_get_stats",
        "foliage_list_sources",
        "foliage_scatter",
        "get_actor_bone_transform",
        "get_actor_bounds",
        "get_actor_properties",
        "get_actors_in_radius",
        "get_all_actors",
        "get_anim_sequence_info",
        "get_asset_info",
        "get_data_table_info",
        "get_data_table_rows",
        "get_dependencies",
        "get_device_settings",
        "get_editor_stats",
        "get_entity_component_property",
        "get_entity_info",
        "get_fort_actor_info",
        "get_ground_z",
        "get_ik_retargeter_info",
        "get_ik_rig_info",
        "get_level_bounds",
        "get_level_info",
        "get_niagara_component_info",
        "get_niagara_system_info",
        "get_project_info",
        "get_referencers",
        "get_retarget_preset",
        "get_selected_actors",
        "get_selected_assets",
        "get_selected_entities",
        "get_sequence_info",
        "get_skeletal_mesh_info",
        "get_static_mesh_info",
        "get_verse_api",
        "get_verse_editables",
        "get_viewport_camera",
        "get_widget_blueprint_info",
        "ik_retarget_capabilities",
        "import_asset",
        "inspect_creative_device",
        "inspect_verse_device",
        "instantiate_prefab",
        "landscape_create",
        "landscape_get_info",
        "landscape_list",
        "landscape_rename",
        "landscape_sculpt",
        "list_actor_classes",
        "list_actor_components",
        "list_assets",
        "list_creative_devices",
        "list_data_tables",
        "list_entities",
        "list_fort_actors",
        "list_niagara_systems",
        "list_scene_component_classes",
        "list_skeleton_bones",
        "list_skeleton_sockets",
        "list_subsystems",
        "list_verse_devices",
        "list_verse_digests",
        "list_verse_modules",
        "list_verse_property_hashes",
        "list_verse_reference_types",
        "list_verse_types",
        "list_widget_bindings",
        "list_widget_blueprints",
        "measure_distance",
        "niagara_capabilities",
        "open_asset_in_uefn",
        "patch_verse_array_entry",
        "pcg_generate",
        "pcg_get_graph_info",
        "play_in_editor",
        "project_memory_append",
        "project_memory_delete",
        "project_memory_get",
        "project_memory_list",
        "project_memory_save",
        "remove_entity_component",
        "remove_retarget_chains",
        "remove_skeleton_socket",
        "remove_widget_binding",
        "remove_widget_from_tree",
        "rename_asset",
        "rename_entity",
        "resize_verse_array",
        "retarget_animation",
        "retarget_animation_pipeline",
        "save_all_dirty",
        "save_asset",
        "save_current_level",
        "save_directory",
        "scene_graph_capabilities",
        "search_assets",
        "search_unreal_api",
        "search_verse_digest",
        "select_actors",
        "select_entities",
        "session_status",
        "set_actor_folder",
        "set_actor_label",
        "set_actor_properties",
        "set_actor_tags",
        "set_actor_transform",
        "set_anim_bone_keys",
        "set_creative_device_fields",
        "set_currency_config_entries",
        "set_device_settings",
        "set_entity_component_property",
        "set_entity_parent",
        "set_widget_property",
        "set_entity_transform",
        "set_mesh_collision",
        "set_niagara_component_parameter",
        "set_object_property",
        "set_retarget_root",
        "set_verse_editable",
        "set_verse_texture_icon",
        "set_viewport_camera",
        "simulate_device_event",
        "skill_read_subskill",
        "snap_actor_to_grid",
        "snap_actor_to_ground",
        "spawn_actor",
        "stop_pie",
        "take_high_res_screenshot",
        "terrain_generate",
        "terrain_get_info",
        "terrain_remove_generated",
        "tester_create_simulation",
        "tester_get_results",
        "tester_list_devices",
        "tester_list_tests",
        "tester_run_simulation",
        "uefn_skill",
        "umg_capabilities",
        "validate_uefn_asset",
        "verse_test_add_case",
        "verse_test_results",
        "verse_test_run",
        "verse_test_scaffold",
        "wire_player_spawners",
        "wire_verse_device_array",
        "wire_verse_device_ref",
        "wire_verse_prop_assets",
        "workspace_compile_verse",
        "workspace_list_verse_errors",
        "workspace_open_verse_file",
        "workspace_push_verse_changes",
        "worldgen_capabilities",
    }
)

_WAIT_FOR_RELOAD_SEC = 8.0


def _manifest_cache_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "UEFN-Ducky" / "mcp_command_manifest.json"


def _registered_tool_names() -> set[str]:
    tm = getattr(mcp, "_tool_manager", None)
    tools = getattr(tm, "_tools", None)
    return set(tools.keys()) if isinstance(tools, dict) else set()


def _fetch_live_manifest() -> Optional[list[dict]]:
    """Live command manifest, waiting briefly if a just-triggered reload is settling."""
    from backend.bridge import send_command

    deadline = time.time() + _WAIT_FOR_RELOAD_SEC
    while True:
        try:
            data = send_command("describe_commands", timeout=4.0)
            commands = data.get("commands")
            return commands if isinstance(commands, list) else None
        except ConnectionError:
            return None  # listener offline — cache will cover it
        except Exception as exc:
            msg = str(exc)
            if "Unknown command" in msg:
                # Pre-describe_commands build: expose names from ping (no docs).
                try:
                    names = send_command("ping", timeout=4.0).get("commands") or []
                    return [{"name": str(n), "doc": "", "params": []} for n in names]
                except Exception:
                    return None
            if time.time() >= deadline:
                return None
            time.sleep(0.7)  # busy (reload in flight) — retry until deadline


def _load_cached_manifest() -> Optional[list[dict]]:
    try:
        data = json.loads(_manifest_cache_path().read_text(encoding="utf-8"))
        commands = data.get("commands")
        return commands if isinstance(commands, list) else None
    except (OSError, ValueError):
        return None


def _save_manifest(commands: list[dict]) -> None:
    try:
        path = _manifest_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"commands": commands, "saved_at": time.time()}),
            encoding="utf-8",
        )
    except OSError:
        pass


def _make_passthrough(command: str, doc: str, param_list: list[dict]) -> Callable[..., str]:
    arg_names = ", ".join(str(p.get("name", "")) for p in param_list if p.get("name")) or "none"

    def _tool(params: Optional[dict] = None, pretty: bool = False) -> str:
        from backend.bridge import send_command

        return tool_json(send_command(command, params or {}), pretty=pretty)

    _tool.__name__ = command
    _tool.__doc__ = (
        f"{doc.strip() or 'UEFN listener command.'} "
        f"(auto-exposed listener command; pass arguments as one `params` dict — keys: {arg_names})"
    )
    return _tool


def register_dynamic_listener_tools(log: Optional[Callable[[str], None]] = None) -> list[str]:
    """Register passthrough tools for listener commands without static wrappers.

    Called at bridge start, before ``mcp.run()``. Best-effort: never raises.
    Returns the tool names it added.
    """
    added: list[str] = []
    try:
        manifest = _fetch_live_manifest()
        if manifest is not None:
            _save_manifest(manifest)
        else:
            manifest = _load_cached_manifest()
        if not manifest:
            return added

        existing = _registered_tool_names()
        for cmd in manifest:
            name = str(cmd.get("name") or "")
            if not name or name in existing or name in _NEVER_EXPOSE:
                continue
            try:
                fn = _make_passthrough(name, str(cmd.get("doc") or ""), list(cmd.get("params") or []))
                mcp.tool()(fn)
                existing.add(name)
                added.append(name)
            except Exception:
                continue
        if log is not None and added:
            log(f"dynamic tools: +{len(added)} listener commands ({', '.join(added[:8])}…)")
    except Exception:
        pass
    return added
