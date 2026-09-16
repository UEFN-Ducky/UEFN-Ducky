"""Exercise both network transports with a real loopback MCP server."""

import socket
import threading
import time

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP

from backend.mcp_plugins import client_pool as cp


@pytest.mark.parametrize("transport", ["http", "sse"])
def test_real_network_session_reconnects_and_shuts_down(monkeypatch, transport):
    mcp = FastMCP("lifecycle-test", host="127.0.0.1")

    @mcp.tool()
    def echo(value: str) -> str:
        return value

    app = mcp.streamable_http_app() if transport == "http" else mcp.sse_app()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, loop="asyncio", log_level="critical", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    pool = cp.PluginClientPool(own_loop=True)
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        path = "/mcp" if transport == "http" else "/sse"
        monkeypatch.setattr(cp, "load_plugin_manifest", lambda pid: {"id": pid, "tool_prefix": pid})
        monkeypatch.setattr(cp, "resolve_server_block", lambda manifest: {
            "type": transport, "url": f"http://127.0.0.1:{port}{path}",
        })
        for _ in range(2):
            tools = pool.run_sync(pool.list_tools_for_plugin("local"))
            assert [tool.name for tool in tools] == ["local__echo"]

            async def call():
                session = await pool._ensure_session(await pool._get_or_create("local"))
                return await session.call_tool("echo", {"value": "owner survived"})

            assert pool.run_sync(call()).content[0].text == "owner survived"
            pool.close_plugin("local")
            assert not pool._owners
    finally:
        try:
            pool.shutdown_sync()
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            sock.close()
    assert not thread.is_alive()
    assert pool._closed
    assert not pool._owners
