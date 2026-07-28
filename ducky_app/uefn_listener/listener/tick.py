"""Slate post-tick: drain main-thread jobs and MCP command queue."""

import queue
import time
import traceback

import unreal

from listener import lookup
from listener.config import STALE_CLEANUP_SEC, STUCK_INFLIGHT_SEC, heavy_tick_limit, tick_batch_limit
from listener.registry import scene_graph


# One editor transaction per call — batching several per frame freezes the Details panel.
# Any command that mutates the editor or does heavy reflection belongs here.
_HEAVY_COMMANDS = frozenset(
    {
        # Verse
        "get_verse_editables",
        "list_verse_property_hashes",
        "wire_verse_device_ref",
        "wire_verse_device_array",
        "wire_verse_prop_assets",
        "set_verse_editable",
        "wire_player_spawners",
        "patch_verse_array_entry",
        "resize_verse_array_field",
        "set_verse_texture_icon",
        "set_currency_config_entries",
        # Actors / level
        "spawn_actor",
        "delete_actors",
        "duplicate_actor",
        "set_actor_transform",
        "set_actor_properties",
        "set_actor_label",
        "set_actor_folder",
        "set_actor_tags",
        "attach_actor",
        "snap_actor_to_ground",
        "snap_actor_to_grid",
        "align_actors",
        "distribute_actors",
        "save_current_level",
        "save_all_dirty",
        # Creative
        "set_device_settings",
        "set_creative_device_fields",
        # Materials / Niagara / data / assets
        "create_material",
        "create_material_instance",
        "duplicate_material",
        "connect_material_nodes",
        "disconnect_material_nodes",
        "connect_material_output",
        "set_material_instance_scalar",
        "set_material_instance_vector",
        "set_material_instance_texture",
        "recompile_material",
        "assign_material_to_mesh",
        "add_material_expression",
        "delete_material_expression",
        "clear_material_expressions",
        "set_material_expression_property",
        "set_material_flags",
        "layout_material_expressions",
        "create_niagara_system",
        "set_niagara_component_parameter",
        "control_niagara_actor",
        "create_widget_blueprint",
        "add_widget_to_tree",
        "remove_widget_from_tree",
        "set_widget_property",
        "add_widget_binding",
        "remove_widget_binding",
        "get_widget_blueprint_info",
        "fill_data_table_from_json",
        "fill_data_table_from_csv",
        "create_data_table",
        "rename_asset",
        "delete_asset",
        "duplicate_asset",
        "save_asset",
        "import_asset",
        "export_asset",
        "preview_static_mesh",
        "fixup_redirectors",
        "save_directory",
        "create_folder",
        # Animation / PCG / editor
        "create_ik_rig_asset",
        "create_ik_retargeter_asset",
        "set_retarget_root",
        "add_retarget_chains",
        "remove_retarget_chains",
        "auto_map_retarget_chains",
        "retarget_animation",
        "retarget_animation_pipeline",
        "add_skeleton_socket",
        "remove_skeleton_socket",
        "pcg_generate",
        "terrain_generate",
        "terrain_remove_generated",
        "foliage_scatter",
        "foliage_clear_generated",
        "landscape_sculpt",
        "area_create",
        "blockout_layout",
        "list_scene_component_classes",
        "exec_console_command",
        "take_high_res_screenshot",
        "play_in_editor",
        "stop_pie",
        "set_object_property",
        "set_viewport_camera",
        "execute_python",
        "reload_listener",
    }
)
from listener.dispatch import dispatch
from listener.logutil import log_msg
from listener.save_coalesce import flush_pending_save
from listener.state import accept_lock, command_queue, main_queue, metrics, response_events, responses, responses_lock


def _clear_in_flight() -> None:
    with accept_lock:
        unreal._mcp_in_flight = False
        unreal._mcp_in_flight_since = 0.0
        unreal._mcp_dispatching = False


def _self_heal_stuck_inflight() -> None:
    """Clear orphaned in_flight after HTTP client timeout (command finished or never ran).

    Never clears while ``_mcp_dispatching`` is true — that means the editor is still
    inside dispatch() and clearing would let another heavy command pile on.
    """
    with accept_lock:
        if not unreal._mcp_in_flight:
            return
        if getattr(unreal, "_mcp_dispatching", False):
            return
        if command_queue.qsize() > 0:
            return
        since = float(getattr(unreal, "_mcp_in_flight_since", 0.0) or 0.0)
        if since <= 0.0:
            return
        age = time.time() - since
        if age < STUCK_INFLIGHT_SEC:
            return
        unreal._mcp_in_flight = False
        unreal._mcp_in_flight_since = 0.0
    log_msg(f"Self-heal: cleared stuck in_flight after {age:.0f}s (no dispatch, empty queue)", "warning")
    metrics["last_error"] = f"self_heal_cleared_inflight_after_{int(age)}s"


def tick_handler(delta_time: float) -> None:
    lookup.invalidate()
    scene_graph.invalidate()
    _self_heal_stuck_inflight()

    while not main_queue.empty():
        try:
            fn = main_queue.get_nowait()
            fn()
        except queue.Empty:
            break
        except Exception as e:
            log_msg(f"Main-thread task error: {e}", "error")

    processed = 0
    heavy_processed = 0
    batch_limit = tick_batch_limit()
    heavy_limit = heavy_tick_limit()
    while not command_queue.empty() and processed < batch_limit:
        try:
            req_id, command, params = command_queue.get_nowait()
        except queue.Empty:
            break

        if command in _HEAVY_COMMANDS and heavy_processed >= heavy_limit:
            command_queue.put((req_id, command, params))
            break

        t0 = time.time()
        unreal._mcp_dispatching = True
        try:
            result = dispatch(command, params)
            response = {"success": True, "result": result}
        except Exception as e:
            log_msg(f"Command '{command}' failed: {e}", "error")
            response = {"success": False, "error": str(e), "traceback": traceback.format_exc()}
            metrics["total_errors"] += 1
            metrics["last_error"] = str(e)
            result = None
        finally:
            unreal._mcp_dispatching = False

        elapsed_ms = (time.time() - t0) * 1000
        metrics["total_requests"] += 1
        metrics["last_request_at"] = time.time()
        metrics["last_command"] = command
        metrics["response_times_ms"].append(elapsed_ms)
        if len(metrics["response_times_ms"]) > 100:
            metrics["response_times_ms"].pop(0)

        # Cross-tick screenshot: keep the HTTP waiter alive, free in_flight, finish later.
        deferred = (
            isinstance(result, dict)
            and result.get("_ducky_defer")
            and str(result.get("capture_id") or "").strip()
        )
        if deferred:
            try:
                from listener.registry.editor_control import bind_deferred_screenshot

                bound = bind_deferred_screenshot(str(result["capture_id"]), req_id)
            except Exception as e:
                log_msg(f"Deferred screenshot bind failed: {e}", "error")
                bound = False
            if bound:
                _clear_in_flight()
                if command in _HEAVY_COMMANDS:
                    heavy_processed += 1
                processed += 1
                continue
            # Fall through to normal failure if bind failed.
            response = {
                "success": False,
                "error": "Failed to defer screenshot capture",
                "result": result,
            }

        ev = None
        with responses_lock:
            responses[req_id] = response
            ev = response_events.pop(req_id, None)
        if ev is not None:
            ev.set()
        # Own the clear under accept_lock so a POST cannot race between clear and accept.
        _clear_in_flight()
        if command in _HEAVY_COMMANDS:
            heavy_processed += 1
        processed += 1

    try:
        from listener.registry.editor_control import pump_deferred_screenshots

        pump_deferred_screenshots()
    except Exception as e:
        log_msg(f"Deferred screenshot pump error: {e}", "error")

    flush_pending_save()

    now = time.time()
    with responses_lock:
        stale = [k for k in responses if float(k.split("_")[2]) / 1e9 < now - STALE_CLEANUP_SEC]
        for k in stale:
            del responses[k]
