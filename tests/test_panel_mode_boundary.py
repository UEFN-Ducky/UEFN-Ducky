import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from backend.agent import run_context, tools
from backend.agent.toolsets.plan_safe import mode_tool_block_reason
from backend.tools.panel import ducky_panel as panel


@pytest.fixture
def boundary(monkeypatch):
    catalog = {n: NS(name=n, description="sample", inputSchema={"type": "object"})
               for n in ("ducky_call_tool", "workspace_git", "workspace_read_file",
                         "workspace_write_file", "ducky_create_plan", "ducky_plan_update_node",
                         "ducky_ask_user", "docs__get_page")}
    reached = []
    async def listing():
        return list(catalog.values())
    async def execute(name, arguments):
        reached.append((name, arguments))
        return NS(ok=True, data='{"ok":true}', error=None, hint="")
    monkeypatch.setattr(tools, "list_mcp_tools", listing)
    monkeypatch.setattr(tools, "execute_tool", execute)
    return catalog, reached


@pytest.mark.parametrize("mode", ["ask", "plan"])
@pytest.mark.parametrize("name", ["workspace_git", "docs__get_page"])
def test_discovery_uses_execution_policy(boundary, mode, name):
    catalog, reached = boundary
    token = run_context.set_mode(mode)
    try:
        row = json.loads(asyncio.run(panel.ducky_get_tools(name=name)))
        expected = mode_tool_block_reason(mode, name, {}, catalog, discovery=True)
        assert row["allowed"] is False
        assert row["mode_reason"] == row["hint"] == expected
        assert row["inputSchema"] == catalog[name].inputSchema
    finally:
        run_context.reset_mode(token)
    assert reached == []


@pytest.mark.parametrize("mode", ["ask", "plan"])
@pytest.mark.parametrize("name,args", [
    ("workspace_git", {"command": "reset --hard"}),
    ("mcp__uefn__workspace_git", {"command": "status"}),
    ("docs__get_page", {}),
    ("workspace_read_file", []),
    ("workspace_read_file", {"value": float("nan")}),
])
def test_nested_guard_uses_actual_arguments_before_execute(boundary, mode, name, args):
    catalog, reached = boundary
    token = run_context.set_mode(mode)
    try:
        expected = mode_tool_block_reason(mode, "ducky_call_tool", {"name": name, "arguments": args}, catalog)
        result = json.loads(asyncio.run(panel.ducky_call_tool(name=name, arguments=args)))
        assert result["ok"] is False
        assert result["error"] == expected
    finally:
        run_context.reset_mode(token)
    assert reached == []


@pytest.mark.parametrize("mode,name", [
    ("ask", "workspace_read_file"), ("plan", "workspace_read_file"),
    ("plan", "mcp__uefn__workspace_read_file"), ("plan", "ducky_create_plan"),
    ("plan", "ducky_plan_update_node"), ("plan", "ducky_ask_user"),
    ("agent", "workspace_git"),
])
def test_allowed_nested_arguments_preserved(boundary, mode, name):
    _, reached = boundary
    arguments = {"sample": "unchanged"}
    token = run_context.set_mode(mode)
    try:
        assert json.loads(asyncio.run(panel.ducky_call_tool(name=name, arguments=arguments)))["ok"] is True
    finally:
        run_context.reset_mode(token)
    assert reached == [(name, arguments)]
