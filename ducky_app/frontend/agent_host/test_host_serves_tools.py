"""An agent host serves its agents' Ducky tools in-process: no tool-server process per host."""

from __future__ import annotations

import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from backend import process_role
from backend.bridge import shared_mcp
from frontend.agent_host import tool_server


class HostMcp:
    """Answers each call with who made it and the role the call runs under."""

    _ducky_skip_plugin_wait = True

    def __init__(self) -> None:
        tools = [SimpleNamespace(name=n, description=n, inputSchema={"type": "object"}) for n in ("whoami", "boom")]
        self._tool_manager = SimpleNamespace(_tools={t.name: t for t in tools})

    async def list_tools(self):
        return list(self._tool_manager._tools.values())

    async def call_tool(self, name, arguments):
        from backend.workspace.identity import current

        if name == "boom":
            raise RuntimeError("tool blew up")
        return [{"type": "text", "text": json.dumps({"conv_id": current().conv_id, "pid": os.getpid(),
                                                     "role": process_role.role()})}]


@pytest.fixture
def host(monkeypatch, tmp_path):
    monkeypatch.delenv("UEFN_DUCKY_SHARED_MCP", raising=False)
    monkeypatch.setenv(shared_mcp.SLOT_ENV, "4")
    monkeypatch.setenv(process_role.ENV, "agent_host")
    monkeypatch.setattr("frontend.settings.default_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(tool_server, "_server", HostMcp)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("started a tool-server process"))
    shared_mcp.reset_for_tests()
    yield tmp_path
    tool_server.stop()
    shared_mcp.reset_for_tests()


def _agent(conv_id: str):
    state = shared_mcp.read_state()
    sock = shared_mcp.connect_and_hello(token=state["token"], key=state["key"], identity={"conv_id": conv_id},
                                        timeout_s=8, host=state["host"], port=int(state["port"]))
    sock.settimeout(15)
    return sock


def _call(sock, name: str, req_id: int) -> dict:
    shared_mcp.write_frame(sock, {"op": "mcp", "id": req_id, "method": "tools/call",
                                  "params": {"name": name, "arguments": {}}})
    while True:
        frame = shared_mcp.read_frame(sock)
        if frame.get("id") == req_id:
            return frame


def test_two_host_agents_get_their_tools_from_the_host_process(host) -> None:
    assert shared_mcp.start_daemon_from_app()["ok"] is True  # the host's readiness gate
    state = json.loads((host / "shared_mcp-h4.json").read_text(encoding="utf-8"))
    assert state["pid"] == os.getpid()  # the host itself serves
    first, second = _agent("c1"), _agent("c2")
    try:
        one = json.loads(_call(first, "whoami", 1)["result"]["content"][0]["text"])
        two = json.loads(_call(second, "whoami", 1)["result"]["content"][0]["text"])
    finally:
        for sock in (first, second):
            shared_mcp.write_frame(sock, {"op": "bye"})
            shared_mcp.close_handle(sock)
    assert (one["conv_id"], two["conv_id"]) == ("c1", "c2")  # each call is its own agent's
    assert one["pid"] == two["pid"] == os.getpid()
    assert one["role"] == "tool_server"  # a call acts as the tool server it replaced
    assert process_role.role() == "agent_host"  # the host itself stays a host
    assert shared_mcp.start_daemon_from_app()["started"] is False  # once per host


def test_a_tool_that_raises_leaves_the_host_serving(host) -> None:
    assert shared_mcp.start_daemon_from_app()["ok"] is True
    sock = _agent("c1")
    try:
        failed = _call(sock, "boom", 1)
        assert "tool blew up" in json.dumps(failed)
        assert shared_mcp.serving()
        assert json.loads(_call(sock, "whoami", 2)["result"]["content"][0]["text"])["conv_id"] == "c1"
    finally:
        shared_mcp.write_frame(sock, {"op": "bye"})
        shared_mcp.close_handle(sock)


def test_the_hosts_tool_server_never_idles_out_and_stops_with_the_host(host, monkeypatch) -> None:
    import time

    monkeypatch.setattr(shared_mcp, "_IDLE_EXIT_S", 0.1)
    assert shared_mcp.start_daemon_from_app()["ok"] is True
    sock = _agent("c1")
    shared_mcp.write_frame(sock, {"op": "bye"})
    shared_mcp.read_frame(sock)
    shared_mcp.close_handle(sock)
    time.sleep(0.5)  # several idle windows with no agent
    assert shared_mcp.serving()
    tool_server.stop()
    assert not shared_mcp.serving()
    assert not (host / "shared_mcp-h4.json").exists()


def test_a_long_tool_called_through_ducky_call_tool_does_not_hold_up_another_agent(host, monkeypatch, request) -> None:
    """Nov 18 2026: one chat's road_network_pipeline calls (45-78 s each, made through
    ducky_call_tool) ran on the host's one loop. Another chat's two file edits took 54 s, until
    the call returned, and its next turn started only then."""
    import threading
    import time

    import backend.agent.tools as tools
    from backend.uefn_plugins.plugin_host.testing import patch_host

    request.getfixturevalue("unrestricted_tools")
    patch_host(monkeypatch, "is_plugin_host_only_tool", lambda _n: False)

    class SlowMcp(HostMcp):
        def __init__(self) -> None:
            super().__init__()
            registered = self._tool_manager._tools
            registered["ducky_call_tool"] = SimpleNamespace(
                name="ducky_call_tool", description="", inputSchema={"type": "object"}, is_async=True)
            registered["workspace_read_file"] = SimpleNamespace(
                name="workspace_read_file", description="", inputSchema={"type": "object"}, is_async=False)

        async def call_tool(self, name, arguments):
            if name == "ducky_call_tool":  # as the real one: the tool layer runs the inner tool
                result = await tools.execute_tool(str(arguments.get("name")), {})
                return [{"type": "text", "text": json.dumps({"ok": result.ok, "error": result.error})}]
            if name == "workspace_read_file":
                time.sleep(1.5)  # a sync tool's function: FastMCP calls it right where it is awaited
                return [SimpleNamespace(text='{"ok": true}')]
            return await super().call_tool(name, arguments)

    mcp = SlowMcp()
    monkeypatch.setattr(tool_server, "_server", lambda: mcp)
    monkeypatch.setattr(tools, "_ensure_mcp", lambda: mcp)
    assert shared_mcp.start_daemon_from_app()["ok"] is True
    builder, other = _agent("city-builder"), _agent("another-chat")
    long_answer: list[dict] = []

    def _long_call() -> None:
        shared_mcp.write_frame(builder, {"op": "mcp", "id": 1, "method": "tools/call", "params": {
            "name": "ducky_call_tool", "arguments": {"name": "workspace_read_file", "arguments": {}}}})
        while True:
            frame = shared_mcp.read_frame(builder)
            if frame.get("id") == 1:
                long_answer.append(frame)
                return

    caller = threading.Thread(target=_long_call, daemon=True)
    try:
        caller.start()
        time.sleep(0.3)  # the long call is running now
        began = time.monotonic()
        quick = json.loads(_call(other, "whoami", 2)["result"]["content"][0]["text"])
        waited = time.monotonic() - began
        assert quick["conv_id"] == "another-chat"
        assert waited < 0.8, waited  # it waited the long call out (1.2 s more) before
        assert not long_answer  # which is still running
        caller.join(timeout=10)
        assert json.loads(long_answer[0]["result"]["content"][0]["text"]) == {"ok": True, "error": ""}
    finally:
        for sock in (builder, other):
            shared_mcp.close_handle(sock)
