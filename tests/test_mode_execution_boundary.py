"""Host-bound modes gate actual dispatch, not provider-supplied mode hints."""
import asyncio
from types import SimpleNamespace as NS

import pytest

from backend.agent import run_context
from backend.agent.toolsets.plan_safe import mode_tool_block_reason
from backend.server import ProtectedFastMCP
from mcp.server.fastmcp.exceptions import ToolError
from test_ducky_mode_dispatch import dispatch, invoke


def catalog():
    return {n: NS(name=n) for n in (
        "workspace_read_file", "workspace_write_file", "workspace_git",
        "terminal_exec", "execute_python", "ducky_call_tool", "ducky_create_plan",
        "ducky_plan_update_node", "ducky_ask_user", "ducky_rename_self",
    )}


@pytest.mark.parametrize("mode", ["ask", "plan"])
@pytest.mark.parametrize("name", ["workspace_write_file", "workspace_git", "terminal_exec", "execute_python"])
@pytest.mark.parametrize("nested", [False, True])
def test_restricted_policy_checks_actual_target(mode, name, nested):
    args = {"mode": "agent"}
    if nested:
        name, args = "ducky_call_tool", {"name": name, "arguments": args}
    assert f"blocked in {mode.title()} mode" in mode_tool_block_reason(mode, name, args, catalog())


@pytest.mark.parametrize("name", ["ducky_create_plan", "ducky_plan_update_node", "ducky_ask_user", "ducky_rename_self"])
def test_local_plan_bookkeeping_only(name):
    assert not mode_tool_block_reason("plan", name, {}, catalog())
    assert "blocked in Ask mode" in mode_tool_block_reason("ask", name, {}, catalog())


@pytest.mark.parametrize("mode", ["ask", "plan"])
def test_server_denies_before_naming_and_dispatch(monkeypatch, mode):
    from backend.workspace import identity
    from backend.agent import chat_title
    from backend.panel import rpc
    server = ProtectedFastMCP("test-mode-boundary")
    reached = []
    @server.tool()
    def workspace_write_file(mode: str = "agent") -> str:
        reached.append(mode)
        return "written"
    def forbidden(*args, **kw):
        raise AssertionError("prerequisite reached before denial")
    monkeypatch.setattr(chat_title, "require_self_name", forbidden)
    monkeypatch.setattr(rpc, "wait_for_question_answers", forbidden)
    token = run_context.set_mode(mode)
    who = identity.bind(identity.RunContext(conv_id="synthetic", run_id="synthetic"))
    try:
        with pytest.raises(ToolError, match=f"blocked in {mode.title()} mode"):
            asyncio.run(server.call_tool("workspace_write_file", {"mode": "agent"}))
    finally:
        identity.reset(who)
        run_context.reset_mode(token)
    assert reached == []


@pytest.mark.parametrize("mode,name", [("ask", "workspace_read_file"), ("plan", "workspace_read_file"), ("plan", "ducky_create_plan"), ("agent", "workspace_write_file")])
def test_server_preserves_allowed_dispatch(monkeypatch, mode, name):
    from backend.workspace import identity
    from backend.agent import verify_evidence
    server = ProtectedFastMCP("test-mode-boundary")
    reached = []
    def operation() -> str:
        reached.append(name)
        return "synthetic result"
    server.add_tool(operation, name=name)
    monkeypatch.setattr(identity, "resolve_context", lambda: None)
    monkeypatch.setattr(verify_evidence, "record_result", lambda *a: None)
    token = run_context.set_mode(mode)
    try:
        asyncio.run(server.call_tool(name, {}))
    finally:
        run_context.reset_mode(token)
    assert reached == [name]


@pytest.mark.parametrize("arguments", [None, [], {"name": "workspace_write_file", "arguments": []}, {"name": "unknown", "arguments": {}}])
def test_plan_invalid_nested_target_refuses(arguments):
    assert "blocked in Plan mode" in mode_tool_block_reason("plan", "ducky_call_tool", arguments, catalog())


@pytest.mark.parametrize("mode", ["ask", "plan"])
@pytest.mark.parametrize("name", ["workspace_write_file", "terminal_exec", "workspace_git"])
def test_server_nested_refuses_before_wrapper_handler(monkeypatch, mode, name):
    server = ProtectedFastMCP("nested-boundary")
    reached = []
    def operation() -> str:
        reached.append("target")
        return "effect"
    def wrapper(name: str, arguments: dict) -> str:
        reached.append("wrapper")
        return operation()
    server.add_tool(operation, name=name)
    server.add_tool(wrapper, name="ducky_call_tool")
    token = run_context.set_mode(mode)
    try:
        with pytest.raises(ToolError, match=f"blocked in {mode.title()} mode"):
            asyncio.run(server.call_tool("ducky_call_tool", {"name": name, "arguments": {}}))
    finally:
        run_context.reset_mode(token)
    assert reached == []


def test_concurrent_server_modes_do_not_leak(monkeypatch):
    from backend.workspace import identity
    from backend.agent import verify_evidence
    monkeypatch.setattr(identity, "resolve_context", lambda: None)
    monkeypatch.setattr(verify_evidence, "record_result", lambda *a: None)
    server = ProtectedFastMCP("concurrent-boundary")
    reached = []
    @server.tool()
    def workspace_write_file() -> str:
        reached.append(run_context.current_mode())
        return "effect"
    async def call(mode):
        token = run_context.set_mode(mode)
        try:
            await asyncio.sleep(0)
            if mode == "agent":
                await server.call_tool("workspace_write_file", {})
            else:
                with pytest.raises(ToolError, match=f"blocked in {mode.title()} mode"):
                    await server.call_tool("workspace_write_file", {})
        finally:
            run_context.reset_mode(token)
    async def both():
        await asyncio.gather(*(call(m) for m in ("ask", "agent", "plan")))
    asyncio.run(both())
    assert reached == ["agent"]
    assert run_context.current_mode() == "agent"


def test_discovery_keeps_mutator_schema_visible():
    server = ProtectedFastMCP("visible-boundary")
    @server.tool()
    def workspace_write_file(path: str, content: str) -> str:
        raise AssertionError("discovery must not invoke")
    token = run_context.set_mode("plan")
    try:
        rows = asyncio.run(server.list_tools())
    finally:
        run_context.reset_mode(token)
    row = next(r for r in rows if r.name == "workspace_write_file")
    assert set(row.inputSchema["required"]) == {"path", "content"}
    assert "blocked in Plan mode" in mode_tool_block_reason("plan", row.name, {}, {row.name: row}, discovery=True)


@pytest.mark.parametrize("name", ["workspace_write_file", "workspace_git", "docs__write_page"])
@pytest.mark.parametrize("inner", [False, True])
def test_embedded_plan_blocks_direct_and_alias_before_transport(dispatch, monkeypatch, name, inner):
    monkeypatch.setattr("backend.agent.coding_agents.plans.plan_mutator_block_reason", dispatch.forbidden)
    monkeypatch.setattr("backend.agent.hammer_guard.note_failure", dispatch.forbidden)
    result = invoke("mcp__uefn__" + name if "__" not in name else "mcp__" + name,
                    {}, mode="plan", inner=inner)
    assert not result.ok
    assert "blocked in Plan mode" in result.error
    assert dispatch.reached == []


@pytest.mark.parametrize("mode,name", [("plan", "ducky_create_plan"), ("plan", "workspace_read_file"), ("agent", "workspace_write_file")])
@pytest.mark.parametrize("nested", [False, True])
def test_embedded_allowed_calls_still_reach_transport(dispatch, mode, name, nested):
    target, args = ("ducky_call_tool", {"name": name, "arguments": {}}) if nested else (name, {})
    result = invoke(target, args, mode=mode)
    assert result.ok, result.error
    assert dispatch.reached == [(name, {})]


@pytest.mark.parametrize("mode", ["ask", "plan"])
def test_catalog_failure_refuses_without_failure_repair(dispatch, monkeypatch, mode):
    from backend.agent import tools
    async def unavailable():
        raise RuntimeError("synthetic private diagnostic")
    monkeypatch.setattr(tools, "list_mcp_tools", unavailable)
    monkeypatch.setattr("backend.agent.hammer_guard.note_failure", dispatch.forbidden)
    result = invoke("workspace_read_file", {}, mode=mode)
    assert result.error == f"blocked in {mode.title()} mode: tool catalog unavailable"
    assert dispatch.reached == []
