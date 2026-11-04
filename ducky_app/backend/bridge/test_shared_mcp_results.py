"""Shared-daemon tools/call results match the stdio server's, structuredContent included.

str tools return their text as plain content: no outputSchema, no {"result": "<escaped json>"}.
"""

import asyncio
from types import SimpleNamespace

import jsonschema
import pytest
from mcp.types import CallToolRequest, CallToolRequestParams, CallToolResult, TextContent

from backend.bridge import shared_mcp
from backend.bridge.test_shared_mcp import _hello, _rpc, serve


def _server():
    from backend.server import ProtectedFastMCP

    server = ProtectedFastMCP("result-shape-test")
    server._ducky_skip_plugin_wait = True

    @server.tool(name="ducky_get_status")
    def status() -> str:  # str tools (tool_json) are plain text content, no schema
        return '{"listener":"online"}'

    @server.tool(name="workspace_delete_file")
    async def delete(path: str) -> str:
        return f"deleted {path}"

    @server.tool(name="dict_tool")
    async def dict_tool() -> dict[str, int]:  # a typed dict is the structured result itself
        return {"count": 2}

    @server.tool(name="no_schema_tool")
    def no_schema_tool(x: int):
        return f"x={x}"

    @server.tool(name="own_result_tool")
    def own_result_tool():
        return CallToolResult(content=[TextContent(type="text", text="own")], isError=False)

    @server.tool(name="own_error_tool")
    def own_error_tool():
        return CallToolResult(content=[TextContent(type="text", text="listener offline")], isError=True)

    @server.tool(name="failing_tool")
    def failing_tool() -> str:
        raise RuntimeError("listener offline")

    return server


CALLS = [
    ("ducky_get_status", {}),
    ("workspace_delete_file", {"path": "Verse/old.verse"}),
    ("dict_tool", {}),
    ("no_schema_tool", {"x": 3}),
    ("own_result_tool", {}),
    ("own_error_tool", {}),
]


@pytest.fixture
def server(monkeypatch):
    from backend.workspace import ai_ignore, identity

    monkeypatch.setattr(identity, "resolve_context", lambda: None)
    monkeypatch.setattr(ai_ignore, "current_policy", lambda: SimpleNamespace(strict=False))
    return _server()


@pytest.mark.parametrize("name,args", CALLS)
def test_shared_result_matches_stdio_result(server, name, args):
    stdio = server._mcp_server.request_handlers[CallToolRequest]
    ident = shared_mcp.identity_from_payload({})

    async def both():
        dedicated = await stdio(CallToolRequest(
            method="tools/call", params=CallToolRequestParams(name=name, arguments=args)))
        shared = await shared_mcp._call_tool(server, name, args, ident)
        return dedicated.root.model_dump(by_alias=True, exclude_none=True), shared

    dedicated, shared = asyncio.run(both())
    assert shared == dedicated
    schema = {row["name"]: row for row in shared_mcp.list_tools_for_handshake(server)}[name].get("outputSchema")
    if schema and not shared["isError"]:
        # MCP: a tool that declares outputSchema must return matching structuredContent.
        jsonschema.validate(shared["structuredContent"], schema)
    else:
        # An error result, a str tool or a tool with no schema carries none.
        assert name in ("ducky_get_status", "workspace_delete_file", "no_schema_tool",
                        "own_result_tool", "own_error_tool")
        assert "structuredContent" not in shared
    if name == "dict_tool":
        assert shared["structuredContent"] == {"count": 2}


def test_str_tool_result_over_the_daemon_socket(server, monkeypatch, tmp_path):
    thread = serve(server, monkeypatch, tmp_path)
    sock = _hello("run-shape")
    try:
        rows = {row["name"]: row for row in _rpc(sock, "tools/list")["tools"]}
        assert "outputSchema" not in rows["ducky_get_status"]
        assert rows["dict_tool"]["outputSchema"]["type"] == "object"
        result = _rpc(sock, "tools/call", {"name": "ducky_get_status", "arguments": {}}, req_id=2)
        assert result["isError"] is False
        assert "structuredContent" not in result
        assert result["content"] == [{"type": "text", "text": '{"listener":"online"}'}]
        # A raising tool is still a JSON-RPC error, never a success.
        with pytest.raises(RuntimeError, match="listener offline"):
            _rpc(sock, "tools/call", {"name": "failing_tool", "arguments": {}}, req_id=3)
    finally:
        shared_mcp.write_frame(sock, {"op": "bye"})
        shared_mcp.close_handle(sock)
        shared_mcp.request_stop()
        thread.join(timeout=5)
