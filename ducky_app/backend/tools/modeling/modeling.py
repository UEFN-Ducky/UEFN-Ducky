"""Modeling tools: static mesh LODs, collision."""

from __future__ import annotations

from backend.bridge import send_command
from backend.util.json_util import tool_json
from backend.tools.support.plugin_gate import plugin_mcp_tool


@plugin_mcp_tool("modeling")
def get_static_mesh_info(asset_path: str, pretty: bool = False) -> str:
    """Get LOD and collision summary for a static mesh asset."""
    return tool_json(send_command("get_static_mesh_info", {"asset_path": asset_path}), pretty=pretty)


@plugin_mcp_tool("modeling")
def set_mesh_collision(asset_path: str, collision_preset: str = "BlockAll", pretty: bool = False) -> str:
    """Set a simplified collision preset on a static mesh (BlockAll, NoCollision, OverlapAll)."""
    return tool_json(send_command("set_mesh_collision", {"asset_path": asset_path, "collision_preset": collision_preset}), pretty=pretty)
