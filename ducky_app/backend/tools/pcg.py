"""PCG tools: run and inspect Procedural Content Generation graphs."""

from __future__ import annotations

from backend.bridge import send_command
from backend.json_util import tool_json
from backend.tools.plugin_gate import plugin_mcp_tool


@plugin_mcp_tool("leveldesign")
def pcg_generate(actor_path: str, force: bool = False, pretty: bool = False) -> str:
    """Run PCG generate on an actor that has a PCGComponent (force=cleanup + regenerate)."""
    return tool_json(send_command("pcg_generate", {"actor_path": actor_path, "force": force}), pretty=pretty)


@plugin_mcp_tool("leveldesign")
def pcg_get_graph_info(actor_path: str, pretty: bool = False) -> str:
    """Read PCG graph asset metadata from an actor's PCGComponent."""
    return tool_json(send_command("pcg_get_graph_info", {"actor_path": actor_path}), pretty=pretty)
