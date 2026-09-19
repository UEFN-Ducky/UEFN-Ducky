"""Single FastMCP instance shared by all tool modules."""

from mcp.server.fastmcp import FastMCP

from backend.agent.coding_agents.plans import PLAN_PROTOCOL
from backend.agent.hard_rules import AGENT_HARD_RULES

mcp = FastMCP(
    "uefn-ducky",
    instructions=(
        "UEFN Ducky — MCP server for UEFN (Unreal Editor for Fortnite) plus Store desktop "
        "plugins (e.g. blender_*). Desktop-plugin tools do NOT need the UEFN listener.\n\n"
        f"{AGENT_HARD_RULES}\n"
        "**Plans (HARD):** Multi-step diagnose/fix/build (≥2 tool rounds) MUST use "
        f"`ducky_create_plan`. Never substitute a chat prose Fix plan. {PLAN_PROTOCOL}\n\n"
        "**Blender (Store plugin):** when blender tools are enabled and the Blender MCP "
        "socket is up, call blender_* immediately — never wait for UEFN / the Fortnite "
        "listener. Teach Connect only when blender_status reports disconnected.\n\n"
        "**Store plugins:** enabled plugins may register extra tools on this same MCP "
        "server (e.g. discord_*, blender_*). If those tools are listed, call them — do "
        "not claim the integration is unavailable.\n\n"
        "Performance: use compact responses (pretty=false), pagination (offset/limit/fields) on "
        "get_all_actors, list_assets, and search_assets.\n\n"
        "Workspace file tools use UEFN_VSCODE_WORKSPACE_FOLDERS. For tkinter via execute_python, "
        "use get_tk_root() and tk.Toplevel — never tk.Tk()."
    ),
)
