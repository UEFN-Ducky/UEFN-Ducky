"""Ensure Store plugin MCP tools are registered before tools/list / tools/call.

Bridge starts ``mcp.run()`` immediately (Cursor cancels slow handshakes) and
loads Store plugins on a background thread. Without this gate, Cursor's first
``tools/list`` caches a catalog missing Discord/Blender/etc., so later
``tools/call`` rejects those names even after plugins finish loading.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

_log = logging.getLogger("uefn.bridge_plugin_gate")

# Cap wait so a wedged plugin load cannot hang Cursor forever.
_DEFAULT_TIMEOUT_S = 45.0
_installed = False


def wait_until_plugins_loaded(timeout: float = _DEFAULT_TIMEOUT_S) -> bool:
    """Block until the first Store plugin load finishes (or timeout)."""
    from backend.uefn_plugins.host import wait_plugins_loaded

    return wait_plugins_loaded(timeout=timeout)


def clear_mcp_tool_cache(mcp: Any) -> None:
    """Drop FastMCP's ListTools cache so late-registered plugin tools resolve."""
    server = getattr(mcp, "_mcp_server", None)
    cache = getattr(server, "_tool_cache", None) if server is not None else None
    if isinstance(cache, dict):
        cache.clear()


def install_bridge_plugin_gate(mcp: Any, *, timeout: float = _DEFAULT_TIMEOUT_S) -> None:
    """Wrap ListTools / CallTool handlers so they wait for plugin register()."""
    global _installed
    if _installed:
        return
    server = getattr(mcp, "_mcp_server", None)
    if server is None:
        return
    try:
        from mcp.types import CallToolRequest, ListToolsRequest
    except Exception:
        return

    handlers = getattr(server, "request_handlers", None)
    if not isinstance(handlers, dict):
        return

    orig_list = handlers.get(ListToolsRequest)
    orig_call = handlers.get(CallToolRequest)
    if orig_list is None or orig_call is None:
        return

    async def list_tools_after_plugins(req: Any) -> Any:
        await asyncio.to_thread(wait_until_plugins_loaded, timeout)
        clear_mcp_tool_cache(mcp)
        return await orig_list(req)

    async def call_tool_after_plugins(req: Any) -> Any:
        await asyncio.to_thread(wait_until_plugins_loaded, timeout)
        # If the client listed tools before plugins finished, refresh cache once.
        name = ""
        try:
            name = str(getattr(getattr(req, "params", None), "name", "") or "")
        except Exception:
            name = ""
        cache = getattr(server, "_tool_cache", None)
        if name and isinstance(cache, dict) and name not in cache:
            clear_mcp_tool_cache(mcp)
        return await orig_call(req)

    handlers[ListToolsRequest] = list_tools_after_plugins
    handlers[CallToolRequest] = call_tool_after_plugins
    _installed = True
    _log.info("bridge plugin gate installed (timeout=%.0fs)", timeout)
