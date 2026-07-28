"""Creative / Verse device settings tools."""

from __future__ import annotations

from typing import Any

from backend.bridge import send_command
from backend.json_util import tool_json
from backend.tools.plugin_gate import plugin_mcp_tool


@plugin_mcp_tool("uefn")
def list_creative_devices(
    class_filter: str = "",
    label_filter: str = "",
    limit: int = 200,
    pretty: bool = False,
) -> str:
    """List Creative and Verse devices in the level (player spawners, triggers, etc.)."""
    result = send_command(
        "list_creative_devices",
        {"class_filter": class_filter, "label_filter": label_filter, "limit": limit},
    )
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def get_device_settings(
    actor_path: str,
    include_events: bool = False,
    pretty: bool = False,
) -> str:
    """Read all ToyOptions settings on a Creative device (types, enums, allowed values)."""
    result = send_command(
        "get_device_settings",
        {"actor_path": actor_path, "include_events": include_events},
    )
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def set_device_settings(
    actor_path: str,
    properties: dict[str, Any],
    save_level: bool = False,
    pretty: bool = False,
) -> str:
    """Set Creative device settings via actor.set_editor_property (enums, bools, structs).

    Example: set_device_settings("Player 1 Spawn Pad", {
        "PlayAudio": "YES",
        "bRespawnAlivePlayers": false,
        "PlayerTeam": {"team_index": 2, "team_type": "Any"},
    })
    """
    result = send_command(
        "set_device_settings",
        {"actor_path": actor_path, "properties": properties, "save_level": save_level},
    )
    return tool_json(result, pretty=pretty)


