"""MCP tool: what a project's Claude Code chats cost, from Claude Code's own session logs."""

from __future__ import annotations

from backend.server import mcp
from backend.util.json_util import tool_json


@mcp.tool()
def ducky_token_audit(project: str = "", days: int = 7, pretty: bool = False) -> str:
    """What the Claude Code chats of a project cost over the last ``days``, at API list prices:
    calls, tokens, cost by context size (how much went to calls re-reading 400K+), repeat reads
    and the costliest chats. ``project`` is a project folder (default: the one you work in).
    Read-only; it reads ~/.claude/projects logs only."""
    root = (project or "").strip()
    if not root:
        from backend.bridge.client import workspace_roots

        roots = workspace_roots()
        if not roots:
            raise ValueError("No project here: pass project=<the project folder>.")
        root = roots[0]
    from backend.agent.usage_audit import audit_project

    return tool_json(audit_project(root, days), pretty=pretty)
