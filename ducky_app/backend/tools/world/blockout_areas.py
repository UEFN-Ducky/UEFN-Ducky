"""Per-area landscape + greybox blockout MCP tools (leveldesign plugin)."""

from __future__ import annotations

from typing import Optional

from backend.bridge import send_command
from backend.util.json_util import tool_json
from backend.tools.support.plugin_gate import plugin_mcp_tool


@plugin_mcp_tool("leveldesign")
def area_list(pretty: bool = False) -> str:
    """List named Areas/* layouts (origin, slot, has terrain/blockout)."""
    return tool_json(send_command("area_list", {}), pretty=pretty)


@plugin_mcp_tool("leveldesign")
def area_create(
    area_id: str,
    preset: str = "",
    size_uu: float = 12800.0,
    seed: int = 42,
    stamps: Optional[list[dict]] = None,
    replace_terrain: bool = True,
    replace_blockout: bool = True,
    pretty: bool = False,
) -> str:
    """Create one named area: mesh terrain on a 100k-uu slot + optional blockout preset.

    Areas stay far apart (slot grid). Folders: Areas/<id>/{Terrain,Blockout,...}.
    Real Landscape create is unavailable — this uses terrain_generate.
    """
    return tool_json(
        send_command(
            "area_create",
            {
                "area_id": area_id,
                "preset": preset,
                "size_uu": size_uu,
                "seed": seed,
                "stamps": stamps,
                "replace_terrain": replace_terrain,
                "replace_blockout": replace_blockout,
            },
            timeout=180.0,
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("leveldesign")
def blockout_list_presets(pretty: bool = False) -> str:
    """List greybox blockout presets (hub, store, arena, corridor, lobby)."""
    return tool_json(send_command("blockout_list_presets", {}), pretty=pretty)


@plugin_mcp_tool("leveldesign")
def blockout_layout(
    area_id: str,
    preset: str,
    origin: Optional[list[float]] = None,
    replace: bool = True,
    pretty: bool = False,
) -> str:
    """Place/replace Cube greybox walls for an area under Areas/<id>/Blockout."""
    return tool_json(
        send_command(
            "blockout_layout",
            {
                "area_id": area_id,
                "preset": preset,
                "origin": origin,
                "replace": replace,
            },
            timeout=120.0,
        ),
        pretty=pretty,
    )
