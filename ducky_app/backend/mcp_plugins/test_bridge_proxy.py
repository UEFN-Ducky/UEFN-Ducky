"""Nested MCP FastMCP proxy sync."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from backend.mcp_plugins import bridge_proxy


def test_sync_adds_and_removes_proxies() -> None:
    bridge_proxy._PROXY_TOOL_NAMES.clear()

    fake_mcp = MagicMock()
    tm = MagicMock()
    tm._tools = {}
    tm.remove_tool = MagicMock(side_effect=lambda name: tm._tools.pop(name, None))
    fake_mcp._tool_manager = tm

    def _tool_decorator(*, name: str):
        def wrap(fn):
            tm._tools[name] = fn
            return fn

        return wrap

    fake_mcp.tool = MagicMock(side_effect=lambda **kw: _tool_decorator(**kw))

    t1 = SimpleNamespace(name="demo__ping", description="ping", inputSchema={})
    t2 = SimpleNamespace(name="demo__pong", description="pong", inputSchema={})

    pool = MagicMock()
    pool.list_all_plugin_tools = AsyncMock(return_value=[t1, t2])

    with (
        patch.object(bridge_proxy, "mcp", fake_mcp),
        patch.object(bridge_proxy, "on_demand_server_ids", return_value=set()),
        patch("backend.mcp_plugins.store.ensure_plugin_prefix_cache"),
        patch("backend.mcp_plugins.client_pool.get_plugin_pool", return_value=pool),
    ):
        added = asyncio.run(bridge_proxy.sync_nested_mcp_proxies_async())
        assert set(added) == {"demo__ping", "demo__pong"}
        assert bridge_proxy._PROXY_TOOL_NAMES == {"demo__ping", "demo__pong"}
        assert "demo__ping" in tm._tools

        pool.list_all_plugin_tools = AsyncMock(return_value=[t1])
        asyncio.run(bridge_proxy.sync_nested_mcp_proxies_async())
        assert bridge_proxy._PROXY_TOOL_NAMES == {"demo__ping"}
        assert "demo__pong" not in tm._tools
        assert asyncio.iscoroutinefunction(tm._tools["demo__ping"])

        pool.list_all_plugin_tools = AsyncMock(side_effect=ConnectionError("offline"))
        asyncio.run(bridge_proxy.sync_nested_mcp_proxies_async())
        assert bridge_proxy._PROXY_TOOL_NAMES == {"demo__ping"}
        assert "demo__ping" in tm._tools

        pool.list_all_plugin_tools = AsyncMock(return_value=[])
        asyncio.run(bridge_proxy.sync_nested_mcp_proxies_async())
        assert not bridge_proxy._PROXY_TOOL_NAMES

    bridge_proxy._PROXY_TOOL_NAMES.clear()


def test_on_demand_server_is_not_listed_but_still_callable(monkeypatch):
    from mcp.server.fastmcp import FastMCP
    from mcp.types import Tool

    from backend.agent import tools as agent_tools
    from backend.mcp_plugins import registry

    server = FastMCP("test")
    monkeypatch.setattr(bridge_proxy, "mcp", server)
    monkeypatch.setattr(bridge_proxy, "_PROXY_TOOL_NAMES", set())
    monkeypatch.setattr(bridge_proxy, "_clear_list_tools_cache", lambda: None)
    monkeypatch.setattr(registry, "_PREFIX_TO_PLUGIN", {"site": "site", "demo": "demo"})
    monkeypatch.setattr("backend.mcp_plugins.store.ensure_plugin_prefix_cache", lambda: None)
    # Bound at import: once loaded, its real rebuild would drop the "site" prefix above.
    monkeypatch.setattr("backend.agent.toolsets.mcp_plugins.ensure_plugin_prefix_cache", lambda: None)
    schema = {"type": "object"}
    listed = Tool(name="demo__ping", description="ping", inputSchema=schema)
    hidden = Tool(name="site__ops_health", description="health", inputSchema=schema)
    pool = MagicMock()
    pool.list_all_plugin_tools = AsyncMock(return_value=[listed, hidden])
    pool.call_tool = AsyncMock(return_value='{"ok": true}')
    monkeypatch.setattr("backend.mcp_plugins.client_pool.get_plugin_pool", lambda: pool)

    # Default: every enabled server is listed.
    monkeypatch.setattr(bridge_proxy, "on_demand_server_ids", lambda: set())
    asyncio.run(bridge_proxy.sync_nested_mcp_proxies_async())
    assert bridge_proxy._PROXY_TOOL_NAMES == {"demo__ping", "site__ops_health"}

    # On demand: the server's tools leave tools/list ...
    monkeypatch.setattr(bridge_proxy, "on_demand_server_ids", lambda: {"site"})
    asyncio.run(bridge_proxy.sync_nested_mcp_proxies_async())
    assert bridge_proxy._PROXY_TOOL_NAMES == {"demo__ping"}
    assert "site__ops_health" not in server._tool_manager._tools

    # ... but ducky_call_tool's dispatcher still routes the name to the pool.
    monkeypatch.setattr(agent_tools, "_mode_denial", AsyncMock(return_value=None))
    result = asyncio.run(agent_tools._execute_tool_inner("site__ops_health", {}))
    assert result.ok, result.error
    pool.call_tool.assert_awaited_with("site__ops_health", {})


def test_nested_servers_on_demand_by_default_except_epic(monkeypatch):
    cfg = {"mcpServers": {"unreal-mcp": {}, "site": {}, "pinned": {"on_demand": False}, "old": {"on_demand": True}}}
    monkeypatch.setattr("backend.mcp_plugins.store.load_mcp_config", lambda: cfg)
    monkeypatch.setattr("backend.mcp_plugins.store.save_mcp_config", lambda c: None)
    monkeypatch.setattr(bridge_proxy, "schedule_sync_nested_proxies", lambda: None)
    assert bridge_proxy.on_demand_server_ids() == {"site", "old"}
    bridge_proxy.set_server_on_demand("site", False)
    assert bridge_proxy.on_demand_server_ids() == {"old"}
    bridge_proxy.set_server_on_demand("pinned", True)
    assert bridge_proxy.on_demand_server_ids() == {"old", "pinned"}


def test_real_proxy_availability_updates_preserve_annotations(monkeypatch):
    from mcp.server.fastmcp import FastMCP
    from mcp.types import Tool, ToolAnnotations
    server = FastMCP("test")
    monkeypatch.setattr(bridge_proxy, "mcp", server)
    monkeypatch.setattr(bridge_proxy, "_PROXY_TOOL_NAMES", set())
    monkeypatch.setattr(bridge_proxy, "_clear_list_tools_cache", lambda: None)
    monkeypatch.setattr(bridge_proxy, "on_demand_server_ids", lambda: set())
    tool = Tool(name="demo__ping", description="ping", inputSchema={"type": "object"},
                annotations=ToolAnnotations(readOnlyHint=True),
                _meta={"ducky_availability": {"state": "available"}})
    pool = MagicMock()
    pool.list_all_plugin_tools = AsyncMock(return_value=[tool])
    monkeypatch.setattr("backend.mcp_plugins.store.ensure_plugin_prefix_cache", lambda: None)
    monkeypatch.setattr("backend.mcp_plugins.client_pool.get_plugin_pool", lambda: pool)
    asyncio.run(bridge_proxy.sync_nested_mcp_proxies_async())
    registered = server._tool_manager._tools[tool.name]
    schema = registered.parameters.copy()
    tool.meta["ducky_availability"]["state"] = "unavailable"
    tool.description = "ping [Currently unavailable]"
    asyncio.run(bridge_proxy.sync_nested_mcp_proxies_async())
    assert registered.meta["ducky_availability"]["state"] == "unavailable"
    assert registered.annotations.readOnlyHint is True
    assert registered.parameters == schema
    assert "unavailable" in registered.description
