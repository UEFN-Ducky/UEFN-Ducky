"""Inventory outages must not look like successful removal."""
import asyncio
from types import SimpleNamespace

import pytest
from mcp.types import Tool, ToolAnnotations
from backend.mcp_plugins import client_pool as mod


def test_outage_recovery_removal_and_backoff(monkeypatch):
    pool = mod.PluginClientPool()
    ids = ["demo"]
    clock = [100.0]
    calls = []
    answer = [Tool(name="demo__read", inputSchema={"type": "object", "properties": {"x": {"type": "string"}}},
                   annotations=ToolAnnotations(readOnlyHint=True), outputSchema={"type": "object"})]
    monkeypatch.setattr(mod, "ensure_plugin_prefix_cache", lambda: None)
    monkeypatch.setattr(mod, "effective_plugin_ids", lambda: ids)
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock[0])
    async def listing(pid):
        calls.append(pid)
        if isinstance(answer[0], Exception):
            raise answer[0]
        return list(answer)
    monkeypatch.setattr(pool, "list_tools_for_plugin", listing)
    async def run():
        good = await pool.list_all_plugin_tools()
        answer[:] = [OSError("PRIVATE")]
        pool.invalidate_tools_cache()
        stale = await pool.list_all_plugin_tools()
        assert len(stale) == 1
        assert stale[0].inputSchema == good[0].inputSchema
        assert stale[0].annotations == good[0].annotations
        assert stale[0].outputSchema == good[0].outputSchema
        assert stale[0].meta["ducky_availability"]["state"] == "unavailable"
        assert "PRIVATE" not in str(stale)
        await pool.list_all_plugin_tools()
        assert len(calls) == 2
        clock[0] += 31
        answer[:] = good
        assert (await pool.list_all_plugin_tools())[0].meta["ducky_availability"]["state"] == "available"
        ids.clear()
        assert await pool.list_all_plugin_tools() == []
        ids.append("demo")
        answer.clear()
        async def empty(pid): return []
        monkeypatch.setattr(pool, "list_tools_for_plugin", empty)
        assert await pool.list_all_plugin_tools() == []
    asyncio.run(run())


def test_uncertain_call_is_not_replayed(monkeypatch):
    pool = mod.PluginClientPool()
    calls = []
    async def invoke(name, args):
        calls.append(name)
        raise ConnectionError("private")
    conn = SimpleNamespace(last_used=0)
    async def get(pid): return conn
    async def session(c): return SimpleNamespace(call_tool=invoke)
    async def close(c): pass
    monkeypatch.setattr(pool, "_get_or_create", get)
    monkeypatch.setattr(pool, "_ensure_session", session)
    monkeypatch.setattr(pool, "_close_connection", close)
    monkeypatch.setattr(mod, "ensure_plugin_prefix_cache", lambda: None)
    monkeypatch.setattr("backend.mcp_plugins.registry.parse_plugin_tool", lambda n: ("demo", "mutate"))
    with pytest.raises(RuntimeError):
        asyncio.run(pool.call_tool("demo__mutate", {}))
    assert calls == ["mutate"]
