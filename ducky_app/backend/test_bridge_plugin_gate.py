"""Bridge plugin gate: tools/list waits for Store plugins (no mcp.run() block)."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import patch

from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest


def test_wait_plugins_loaded_timeout_false() -> None:
    from backend.uefn_plugins import host as plugins_host

    with (
        patch.object(plugins_host, "_LOADED", False),
        patch.object(plugins_host, "ensure_plugins_loaded_async", lambda: None),
        patch.object(plugins_host._LOAD_DONE, "wait", return_value=False),
    ):
        assert plugins_host.wait_plugins_loaded(timeout=0.01) is False


def test_install_gate_waits_before_list_tools() -> None:
    from backend import bridge_plugin_gate
    from backend.server import mcp
    from mcp.types import ListToolsRequest as LT

    waited: list[float] = []
    listed: list[str] = []

    async def fake_list(_req):
        listed.append("ok")
        return SimpleNamespace(tools=[])

    async def fake_call(_req):
        return SimpleNamespace()

    # Fresh install for this test process.
    bridge_plugin_gate._installed = False
    server = mcp._mcp_server
    prev_list = server.request_handlers.get(LT)
    prev_call = server.request_handlers.get(CallToolRequest)
    server.request_handlers[LT] = fake_list
    server.request_handlers[CallToolRequest] = fake_call

    def _wait(timeout: float = 45.0) -> bool:
        waited.append(timeout)
        return True

    try:
        with patch.object(bridge_plugin_gate, "wait_until_plugins_loaded", _wait):
            bridge_plugin_gate.install_bridge_plugin_gate(mcp, timeout=12.0)
            handler = server.request_handlers[LT]
            asyncio.run(handler(ListToolsRequest()))
    finally:
        if prev_list is not None:
            server.request_handlers[LT] = prev_list
        if prev_call is not None:
            server.request_handlers[CallToolRequest] = prev_call
        bridge_plugin_gate._installed = False

    assert waited == [12.0]
    assert listed == ["ok"]


def test_call_tool_clears_stale_cache_for_missing_name() -> None:
    from backend import bridge_plugin_gate
    from backend.server import mcp

    bridge_plugin_gate._installed = False
    server = mcp._mcp_server
    server._tool_cache.clear()
    server._tool_cache["ping"] = object()

    async def fake_list(_req):
        return SimpleNamespace(tools=[])

    called: list[str] = []

    async def fake_call(req):
        called.append(str(req.params.name))
        return SimpleNamespace()

    prev_list = server.request_handlers[ListToolsRequest]
    prev_call = server.request_handlers[CallToolRequest]
    server.request_handlers[ListToolsRequest] = fake_list
    server.request_handlers[CallToolRequest] = fake_call

    try:
        with patch.object(bridge_plugin_gate, "wait_until_plugins_loaded", lambda timeout=45.0: True):
            bridge_plugin_gate.install_bridge_plugin_gate(mcp)
            handler = server.request_handlers[CallToolRequest]
            req = CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(name="discord_list_bots", arguments={}),
            )
            asyncio.run(handler(req))
            # Missing name → cache cleared before call.
            assert "ping" not in server._tool_cache or "discord_list_bots" in called
    finally:
        server.request_handlers[ListToolsRequest] = prev_list
        server.request_handlers[CallToolRequest] = prev_call
        bridge_plugin_gate._installed = False
        server._tool_cache.clear()

    assert called == ["discord_list_bots"]


def test_run_bridge_still_reaches_mcp_run_fast() -> None:
    """Gate must not put ensure_plugins_loaded on the mcp.run critical path."""
    import frontend.launcher as launcher
    import backend.server as server
    import backend.uefn_plugins.host as host

    ran: list[str] = []

    def _fake_run() -> None:
        ran.append("mcp.run")

    def _slow_ensure() -> None:
        ran.append("ensure_bg")
        time.sleep(30)

    with (
        patch.object(launcher, "_ensure_repo_on_path", return_value="."),
        patch.object(server.mcp, "run", _fake_run),
        patch.object(host, "ensure_plugins_loaded", _slow_ensure),
        patch("frontend.ship_newest.ship_newest_everywhere", lambda **_k: []),
        patch("frontend.appdata_maintenance.start_appdata_maintenance_async", lambda: None),
        patch("backend.dynamic_tools.register_dynamic_listener_tools", lambda: None),
        patch("backend.mcp_plugins.bridge_proxy.sync_nested_mcp_proxies", lambda: []),
        patch("backend.bridge_plugin_gate.install_bridge_plugin_gate", lambda *_a, **_k: None),
    ):
        t0 = time.perf_counter()
        launcher.run_bridge()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert "mcp.run" in ran
    assert elapsed_ms < 5000.0


if __name__ == "__main__":
    test_wait_plugins_loaded_timeout_false()
    test_install_gate_waits_before_list_tools()
    test_call_tool_clears_stale_cache_for_missing_name()
    test_run_bridge_still_reaches_mcp_run_fast()
    print("ok bridge_plugin_gate")
