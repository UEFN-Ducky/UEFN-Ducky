"""Project metadata, level save/info, viewport camera."""

from __future__ import annotations

from typing import Any, Optional

from backend.bridge import send_command
from backend.util.json_util import tool_json
from backend.tools.support.plugin_gate import plugin_mcp_tool


@plugin_mcp_tool("uefn")
def get_project_info(pretty: bool = False) -> str:
    """Get project name and content root (often /{ProjectName}/, not /Game/)."""
    result = send_command("get_project_info")
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def save_current_level(pretty: bool = False) -> str:
    """Save the current level."""
    result = send_command("save_current_level")
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def get_level_info(pretty: bool = False) -> str:
    """Get current level name and actor count."""
    result = send_command("get_level_info")
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def get_viewport_camera(pretty: bool = False) -> str:
    """Get viewport camera position and rotation."""
    result = send_command("get_viewport_camera")
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def set_viewport_camera(
    location: Optional[list[float]] = None,
    rotation: Optional[list[float]] = None,
    pretty: bool = False,
) -> str:
    """Move the viewport camera."""
    params: dict[str, Any] = {}
    if location is not None:
        params["location"] = location
    if rotation is not None:
        params["rotation"] = rotation
    result = send_command("set_viewport_camera", params)
    return tool_json(result, pretty=pretty)
