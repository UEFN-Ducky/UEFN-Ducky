"""With UEFN closed, tools that don't need UEFN run and never touch the listener."""

from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import backend.agent.tools as tools

pytestmark = pytest.mark.usefixtures("unrestricted_tools")

CLOSED_PORT = 1  # nothing listens here

NON_UEFN_TOOLS = (
    "workspace_write_file",
    "workspace_read_file",
    "workspace_git",
    "ducky_terminal_run",
    "ducky_terminal_open",
    "web_search",
    "web_fetch",
    "ducky_plan_update_node",
    "ducky_spawn_chat",
    "list_verse_digests",
)


def test_twenty_non_uefn_calls_make_zero_listener_requests(monkeypatch) -> None:
    requests: list[object] = []
    monkeypatch.setattr("backend.bridge.configured_listener_port", lambda: CLOSED_PORT)
    monkeypatch.setattr(
        "backend.bridge.listener_get_health",
        lambda port, timeout=1.0: requests.append(("health", port)),
    )
    real_connect = socket.create_connection

    def _counting_connect(address, *a, **k):
        if address[1] == CLOSED_PORT:
            requests.append(("connect", address))
        return real_connect(address, *a, **k)

    monkeypatch.setattr(socket, "create_connection", _counting_connect)
    monkeypatch.setattr("backend.uefn_plugins.host.is_plugin_host_only_tool", lambda _n: False)

    ran: list[str] = []

    async def _fake_call(name: str, args: dict):
        ran.append(name)
        return [SimpleNamespace(text='{"ok": true}')]

    mcp = MagicMock()
    mcp.call_tool = _fake_call
    monkeypatch.setattr(tools, "_ensure_mcp", lambda: mcp)

    for i in range(20):
        name = NON_UEFN_TOOLS[i % len(NON_UEFN_TOOLS)]
        result = asyncio.run(tools.execute_tool(name, {}))
        assert result.ok, (name, result.error)
        assert "offline" not in (result.data or "").lower()

    assert len(ran) == 20
    assert requests == []


def test_no_tool_says_listener_offline_before_it_runs(monkeypatch) -> None:
    monkeypatch.setattr("backend.uefn_plugins.host.is_plugin_host_only_tool", lambda _n: False)
    ran: list[str] = []

    async def _fake_call(name: str, args: dict):
        ran.append(name)
        return [SimpleNamespace(text='{"ok": true}')]

    mcp = MagicMock()
    mcp.call_tool = _fake_call
    monkeypatch.setattr(tools, "_ensure_mcp", lambda: mcp)

    # Even a UEFN tool is called: the call itself finds out whether UEFN is there.
    result = asyncio.run(tools.execute_tool("spawn_actor", {}))
    assert result.ok and ran == ["spawn_actor"]
