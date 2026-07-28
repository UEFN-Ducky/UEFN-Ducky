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

    bridge_proxy._PROXY_TOOL_NAMES.clear()
