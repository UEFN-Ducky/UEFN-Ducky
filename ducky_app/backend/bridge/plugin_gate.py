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
    # Callers clear after editing registered tools in place (bridge_proxy's descriptions,
    # annotations, availability): the catalog watchers must look again.
    from backend.bridge.shared_mcp import bump_catalog_revision

    bump_catalog_revision()


def lean_tool_list(tools: list[Any]) -> list[Any]:
    """What tools/list sends a client: Ducky's own tools, never Store-plugin tools.

    Plugin tools (and on-demand nested servers, see bridge_proxy) are found with
    ``ducky_get_tools`` and run with ``ducky_call_tool``, so the list a client loads stays
    the same size however many plugins are installed. Sending every schema grew past
    Codex's 2048-tool limit, and Codex then refused the whole server.
    """
    try:
        from backend.tools.support.plugin_gate import HOST_DISK_TOOLS
        from backend.uefn_plugins.host import store_plugin_tool_names

        hidden = store_plugin_tool_names() - HOST_DISK_TOOLS
    except Exception:
        return tools
    return [t for t in tools if getattr(t, "name", "") not in hidden]


# Arguments the server fills in itself; clients never need to see them.
_HIDDEN_ARGS = frozenset({"pretty"})


def lean_schema(node: Any, *, optional: bool = False) -> Any:
    """The input schema a client is shown: FastMCP noise every listed tool pays for, dropped.

    Drops ``title`` labels, ``additionalProperties: true``, ``default: null``, the ``pretty``
    argument and the ``null`` branch of an optional argument's ``anyOf``. Structure that
    tells the model something (``$ref``/``$defs``, typed ``additionalProperties``, enums,
    real defaults) stays. Calls are still checked against the full registered schema.
    """
    if isinstance(node, list):
        return [lean_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    required = node.get("required")
    required_names = set(required) if isinstance(required, list) else set()
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "title" and isinstance(value, str):
            continue
        if key == "additionalProperties" and value is True:
            continue
        if key == "default" and value is None:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {
                name: lean_schema(prop, optional=name not in required_names)
                for name, prop in value.items()
                if name not in _HIDDEN_ARGS
            }
        elif key == "required" and isinstance(value, list):
            kept = [name for name in value if name not in _HIDDEN_ARGS]
            if kept:
                out[key] = kept
        else:
            out[key] = lean_schema(value)
    any_of = out.get("anyOf")
    if optional and isinstance(any_of, list):
        branches = [branch for branch in any_of if branch != {"type": "null"}]
        if len(branches) == 1 and isinstance(branches[0], dict):
            rest = {k: v for k, v in out.items() if k != "anyOf"}
            return {**branches[0], **rest}
        if branches and len(branches) < len(any_of):
            out["anyOf"] = branches
    return out


def advertised_tool(tool: Any) -> Any:
    """A copy of ``tool`` with its lean schema. Never edits the tool itself: the SDK's
    tools/call cache holds the listed objects and validates arguments with them."""
    schema = getattr(tool, "inputSchema", None)
    if not isinstance(schema, dict) or not hasattr(tool, "model_copy"):
        return tool
    return tool.model_copy(update={"inputSchema": lean_schema(schema)})


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
        from backend.uefn_plugins.host import plugins_ready

        if not plugins_ready():
            await asyncio.to_thread(wait_until_plugins_loaded, timeout)
            # Nested Epic/HTTP proxies register after Store plugins — wait once so
            # Cursor's first tools/list includes unreal__* when Epic :8000 is up.
            try:
                from backend.mcp_plugins.bridge_proxy import (
                    wait_until_nested_proxies_synced,
                )

                await asyncio.to_thread(
                    wait_until_nested_proxies_synced, min(20.0, timeout)
                )
            except Exception:
                pass
            clear_mcp_tool_cache(mcp)
        # The SDK caches the full list for tools/call first, so a hidden tool still runs.
        result = await orig_list(req)
        listed = getattr(result, "root", result)
        if isinstance(getattr(listed, "tools", None), list):
            listed.tools = [advertised_tool(t) for t in lean_tool_list(listed.tools)]
        return result

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
        try:
            from backend.agent.coding_agents.plans import plan_mutator_block_reason

            blocked = plan_mutator_block_reason(name)
        except Exception:
            blocked = None
        if blocked:
            try:
                from mcp.types import CallToolResult, TextContent

                result = CallToolResult(
                    content=[TextContent(type="text", text=blocked)],
                    isError=True,
                )
                try:
                    from mcp.types import ServerResult

                    return ServerResult(root=result)
                except Exception:
                    return result
            except Exception:
                _log.warning("plan tick gate could not format MCP error for %s", name)
        result = await orig_call(req)
        _record_bridge_tool(name, req, result)
        return result

    handlers[ListToolsRequest] = list_tools_after_plugins
    handlers[CallToolRequest] = call_tool_after_plugins
    _installed = True
    _log.info("bridge plugin gate installed (timeout=%.0fs)", timeout)


def _record_bridge_tool(name: str, req: Any, result: Any) -> None:
    """Journal Epic / plugin `_ducky` results from the IDE bridge. Never raises."""
    try:
        if not name or name == "ducky_call_tool":
            return
        params = getattr(req, "params", None)
        args = getattr(params, "arguments", None) if params is not None else None
        if not isinstance(args, dict):
            args = {}
        text, ok = _result_text_ok(result)
        from backend.workspace.tool_record import record_tool_result

        record_tool_result(name, args, text, ok=ok)
    except Exception:
        pass


def _result_text_ok(result: Any) -> tuple[str, bool]:
    root = getattr(result, "root", result)
    is_error = bool(getattr(root, "isError", False) or getattr(result, "isError", False))
    content = getattr(root, "content", None)
    if content is None:
        content = getattr(result, "content", None)
    if isinstance(result, str):
        return result, not is_error
    try:
        from backend.agent.mcp_content import mcp_content_to_text

        text = mcp_content_to_text(content) if content is not None else ""
    except Exception:
        text = ""
    return text, not is_error
