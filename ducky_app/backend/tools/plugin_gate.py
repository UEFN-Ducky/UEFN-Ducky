"""Gate MCP tool bodies on Store desktop-plugin enable + chat allowlist."""

from __future__ import annotations

import functools
from typing import Any, Callable


def require_plugin(plugin_id: str) -> None:
    from backend.uefn_plugins.host import is_plugin_enabled, uefn_agent_tools_allowed

    pid = (plugin_id or "").strip().lower()
    if not pid:
        raise ValueError("plugin gate missing plugin id")
    if not is_plugin_enabled(pid):
        raise ValueError(f"Plugin '{pid}' is disabled — enable it in Settings → Store")
    if not uefn_agent_tools_allowed(pid):
        raise ValueError(f"Plugin '{pid}' tools are off for this chat")


def plugin_mcp_tool(plugin_id: str, **tool_kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Like ``@mcp.tool`` but refuses calls when the Store plugin is off."""
    from backend.server import mcp

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            require_plugin(plugin_id)
            return fn(*args, **kwargs)

        return mcp.tool(**tool_kwargs)(wrapper)

    return deco