"""Fortnite actor discovery tools (read-only inspection of Fort* actors)."""

from __future__ import annotations

from typing import Optional

from backend.bridge import send_command
from backend.util.json_util import tool_json
from backend.tools.support.plugin_gate import plugin_mcp_tool


@plugin_mcp_tool("leveldesign")
def list_fort_actors(class_prefix: str = "Fort", offset: int = 0, limit: Optional[int] = None, fields: Optional[list[str]] = None, pretty: bool = False) -> str:
    """List level actors whose class starts with Fort (devices, props, etc.)."""
    params: dict = {"class_prefix": class_prefix, "offset": offset}
    if limit is not None:
        params["limit"] = limit
    if fields:
        params["fields"] = fields
    return tool_json(send_command("list_fort_actors", params), pretty=pretty)


@plugin_mcp_tool("leveldesign")
def get_fort_actor_info(actor_path: str, properties: Optional[list[str]] = None, pretty: bool = False) -> str:
    """Inspect a Fort* actor (read-only properties)."""
    params: dict = {"actor_path": actor_path}
    if properties:
        params["properties"] = properties
    return tool_json(send_command("get_fort_actor_info", params), pretty=pretty)
