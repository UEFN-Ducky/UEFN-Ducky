"""An MCP server's Settings page: its own tools only, each once, and a switch that answers at once."""

from __future__ import annotations

import asyncio
import threading
import time

from mcp.types import Tool

from frontend.ui_web import mcp_catalog


def _tool(name: str) -> Tool:
    return Tool(name=name, description=f"{name} does a thing", inputSchema={"type": "object", "properties": {}})


class _Pool:
    def __init__(self, tools=None, error: Exception | None = None) -> None:
        self.tools, self.error, self.asked = tools or [], error, []

    async def list_tools_for_plugin(self, plugin_id: str):
        self.asked.append(plugin_id)
        if self.error:
            raise self.error
        return self.tools

    def run_sync(self, coro, timeout=None):
        return asyncio.run(coro)


def test_a_nested_server_lists_only_its_own_tools(monkeypatch) -> None:
    pool = _Pool([_tool("site__acl_can"), _tool("site__ops_health")])
    monkeypatch.setattr("backend.mcp_plugins.client_pool.get_plugin_pool", lambda: pool)
    out = mcp_catalog.build_server_catalog("site")
    assert out["ok"] is True and pool.asked == ["site"]
    assert [t["name"] for t in out["tools"]] == ["site__acl_can", "site__ops_health"] and out["total"] == 2


def test_a_server_that_does_not_answer_says_why(monkeypatch) -> None:
    pool = _Pool(error=TimeoutError("no answer in 45 s"))
    monkeypatch.setattr("backend.mcp_plugins.client_pool.get_plugin_pool", lambda: pool)
    out = mcp_catalog.build_server_catalog("site")
    assert out["ok"] is False and "no answer in 45 s" in out["error"] and out["tools"] == []


def test_each_tool_is_listed_once(monkeypatch) -> None:
    from backend.agent import tools as agent_tools

    class _Mcp:
        async def list_tools(self):
            return [_tool("ducky_get_status"), _tool("site__acl_can")]  # the registry proxies nested tools too

    class _AllPool:
        async def list_all_plugin_tools(self):
            return [_tool("site__acl_can"), _tool("site__ops_health")]

    monkeypatch.setattr(agent_tools, "_ensure_mcp", lambda: _Mcp())
    monkeypatch.setattr("backend.mcp_plugins.client_pool.get_plugin_pool", lambda: _AllPool())
    names = [t.name for t in asyncio.run(agent_tools.list_mcp_tools(apply_filters=False))]
    assert names == ["ducky_get_status", "site__acl_can", "site__ops_health"]


def test_turning_a_server_off_does_not_wait_for_it_to_close(monkeypatch) -> None:
    from backend.mcp_plugins import store

    closing = threading.Event()

    class _SlowPool:
        def invalidate_tools_cache(self) -> None:
            pass

        def close_plugin(self, pid: str) -> None:
            closing.wait(5)  # a stuck server

    monkeypatch.setattr(store, "load_mcp_config", lambda: {"mcpServers": {"slow": {"type": "http", "url": "http://x/mcp"}}})
    saved: list[dict] = []
    monkeypatch.setattr(store, "save_mcp_config", lambda cfg: saved.append(cfg))
    monkeypatch.setattr(store, "get_enabled_plugin_ids", lambda: [])
    monkeypatch.setattr("backend.mcp_plugins.client_pool.get_plugin_pool", lambda: _SlowPool())
    started = time.monotonic()
    try:
        out = store.set_mcp_server_enabled("slow", False)
        assert out["ok"] is True and out["enabled"] is False
        assert time.monotonic() - started < 2
        assert saved[-1]["mcpServers"]["slow"]["disabled"] is True
    finally:
        closing.set()
