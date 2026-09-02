"""Verse @editable property tools."""

from __future__ import annotations

from typing import Any

from backend.bridge import send_command
from backend.util.json_util import tool_json
from backend.tools.support.plugin_gate import plugin_mcp_tool
from backend.tools.verse.wire_preflight import run_with_build_retry


@plugin_mcp_tool("verse")
def list_verse_property_hashes(refresh: bool = False, pretty: bool = False) -> str:
    """List Verse @editable field names mapped to mangled __verse_0x... property names."""
    result = send_command("list_verse_property_hashes", {"refresh": refresh})
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("verse")
def get_verse_editables(
    actor_path: str,
    include_wiring_hints: bool = True,
    pretty: bool = False,
) -> str:
    """Read ALL @editable fields on a Verse device — mandatory before any wire/set.

    Parse the full JSON: `editables` (every field name, `wiring.tool`, `verse_type`,
    `mangled_name`, current `value`), `resolution_tried`, `STOP`, `wiring.can_wire`,
    `verse_source`. STOP is advisory: if `mangled_name` is null, call
    `list_verse_property_hashes(refresh=true)`, re-inspect, then wire. Never ask
    the user to Build Verse, paste T3D, or drag Details. Scalar device refs
    (e.g. NPCSpawner1) use wire_verse_device_ref — not wire_verse_device_array.
    Never assume field names; only use keys returned here.
    """
    result = send_command(
        "get_verse_editables",
        {"actor_path": actor_path, "include_wiring_hints": include_wiring_hints},
    )
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("verse")
def set_verse_editable(
    actor_path: str,
    field: str,
    target_path: str = "",
    value: Any = None,
    pretty: bool = False,
) -> str:
    """Set a Verse @editable field. Device refs: pass target_path (label). Scalars: pass value.

    Example: set_verse_editable("MyDevice", "TargetDevice", target_path="OtherDevice")
    — labels exactly as shown by find_devices, field names from inspect_verse_device.
    """
    params: dict[str, Any] = {"actor_path": actor_path, "field": field}
    if target_path:
        params["target_path"] = target_path
    if value is not None:
        params["value"] = value
    result = run_with_build_retry(
        lambda: send_command("set_verse_editable", params), tool_name="set_verse_editable"
    )
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("verse")
def wire_player_spawners(
    manager_path: str,
    spawn_pad_paths: list[str] | None = None,
    pretty: bool = False,
) -> str:
    """Wire a manager device's AllPlayerSpawners array to spawn pads via wrapper SavedActor.

    Omit spawn_pad_paths to auto-detect pads attached under the manager in the outliner.
    Example: wire_player_spawners("MyPlayerManager")
    """
    params: dict[str, Any] = {"manager_path": manager_path}
    if spawn_pad_paths:
        params["spawn_pad_paths"] = spawn_pad_paths
    result = send_command("wire_player_spawners", params)
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("verse")
def set_verse_texture_icon(
    actor_path: str,
    icon_field: str,
    texture_path: str,
    array_field: str = "",
    entry_index: int = 0,
    pretty: bool = False,
) -> str:
    """Set Assets_texture icon (LevelIcon, CurrencyIcon, …) on a Verse device.

    texture_path: full path or bare name (T_Icon). Array rows: pass array_field + entry_index.
    """
    result = send_command(
        "set_verse_texture_icon",
        {
            "actor_path": actor_path,
            "icon_field": icon_field,
            "texture_path": texture_path,
            "array_field": array_field,
            "entry_index": entry_index,
        },
    )
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("verse")
def list_verse_reference_types(pretty: bool = False) -> str:
    """Catalog Verse @editable reference types (wrapper class + link property + which tool to use).

    Call this when wiring an unfamiliar Verse device — no project scripts required.
    """
    result = send_command("list_verse_reference_types", {})
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("verse")
def wire_verse_device_ref(
    actor_path: str,
    field: str,
    target_path: str,
    pretty: bool = False,
) -> str:
    """Wire one scalar creative-device, creative_prop, or Verse-to-Verse @editable.

    **Call alone** — one per assistant message. Do not parallelize with other wire/spawn tools.

    Creative devices: sets SavedActor on the inner Script wrapper.
    Verse-to-Verse refs (e.g. ?player_manager): auto-routes to set_verse_editable.

    Call get_verse_editables first. STOP is advisory — if a wire fails, refresh
    hashes and retry once. Never abort to the user. Example:
    wire_verse_device_ref("MyDevice", "SomeButtonField", "MyButton")
    """
    result = run_with_build_retry(
        lambda: send_command(
            "wire_verse_device_ref",
            {"actor_path": actor_path, "field": field, "target_path": target_path},
        ),
        tool_name="wire_verse_device_ref",
    )
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("verse")
def wire_verse_device_array(
    actor_path: str,
    field: str,
    target_path: str = "",
    target_paths: list[str] | None = None,
    pretty: bool = False,
) -> str:
    """Wire one or more creative devices / props into an array @editable in one call.

    Pass target_path (one) OR target_paths (1..N, appended in order). Returns
    `wired` (labels) + `count`. For scalar fields like NPCSpawner1 use
    wire_verse_device_ref instead. Then save_current_level when done.
    Example: wire_verse_device_array("MyDevice", "SomeArrayField", target_paths=["Marker_1", "Marker_2"])
    """
    paths = [target_path] if target_path else list(target_paths or [])
    if not paths:
        raise ValueError(
            "wire_verse_device_array needs at least one target: pass target_path or target_paths=[...]. "
            "For scalar spawners (NPCSpawner1, NPCSpawner2, …) use wire_verse_device_ref."
        )
    result = run_with_build_retry(
        lambda: send_command(
            "wire_verse_device_array",
            {"actor_path": actor_path, "field": field, "target_paths": paths},
        ),
        tool_name="wire_verse_device_array",
    )
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("verse")
def wire_verse_prop_assets(
    actor_path: str,
    field: str,
    asset_path: str,
    pretty: bool = False,
) -> str:
    """Wire one creative_prop_asset path into an array @editable (single asset per call).

    Call once per asset; then save_current_level when done.
    Example: wire_verse_prop_assets("MyDevice", "SomePropAssetField", "/Game/.../SomeProp.SomeProp_C")
    """
    result = send_command(
        "wire_verse_prop_assets",
        {"actor_path": actor_path, "field": field, "asset_paths": [asset_path]},
    )
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("verse")
def set_currency_config_entries(
    actor_path: str,
    entries: list[dict[str, Any]] | None = None,
    count: int = 0,
    pretty: bool = False,
) -> str:
    """Create rows in a Verse device's array-of-struct @editable (e.g. wallet CurrencyConfigs).

    entries dicts may include name/CurrencyName and display_order; or pass count for empty rows.
    """
    params: dict[str, Any] = {"actor_path": actor_path}
    if entries is not None:
        params["entries"] = entries
    else:
        params["count"] = count
    result = run_with_build_retry(
        lambda: send_command("set_currency_config_entries", params),
        tool_name="set_currency_config_entries",
    )
    return tool_json(result, pretty=pretty)
