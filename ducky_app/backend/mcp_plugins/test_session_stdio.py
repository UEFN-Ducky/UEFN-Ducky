"""Check the owner against the real MCP SDK and a local child server."""
import asyncio
import sys
from pathlib import Path

from backend.mcp_plugins import client_pool as cp


def test_real_stdio_session_survives_callers_and_closes_cleanly(monkeypatch):
    server = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "mcp_echo_server.py"
    monkeypatch.setattr(cp, "load_plugin_manifest", lambda pid: {"id": pid, "tool_prefix": pid})
    monkeypatch.setattr(cp, "resolve_server_block", lambda manifest: {
        "type": "stdio", "command": sys.executable, "args": ["-B", str(server)],
    })
    pool = cp.PluginClientPool(own_loop=True)
    try:
        assert [tool.name for tool in pool.run_sync(pool.list_tools_for_plugin("local"))] == ["local__echo"]

        async def echo():
            session = await pool._ensure_session(await pool._get_or_create("local"))
            return await session.call_tool("echo", {"value": "owner survived"})

        result = asyncio.run(pool.run_async(echo()))
        assert result.content[0].text == "owner survived"
        pool.close_plugin("local")
        assert not pool._owners
        assert [tool.name for tool in pool.run_sync(pool.list_tools_for_plugin("local"))] == ["local__echo"]
    finally:
        pool.shutdown_sync()
    assert pool._closed
    assert not pool._owners
