"""Actual dispatch and nested dispatcher boundaries, with transport tripwires."""
import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from backend.agent import run_context, tools
from backend.agent.toolsets import plan_safe
from backend.tools.panel import ducky_panel


# Independently reported escapes, plus bookkeeping/delegation and end-of-name edits.
QUALIFIED_MUTATORS = [
    ("external__workspace_git", {"args": ["reset", "--hard"]}),
    ("external__ducky_call_tool", {"name": "workspace_write_file", "arguments": {"path": "mock", "content": "mock"}}),
    ("external__ducky_create_plan", {"title": "mock"}),
    ("external__ducky_plan_delete_node", {"node_id": "mock"}),
    ("docs__read_and_delete_page", {}),
    ("assets__asset_save", {}),
    ("world__actor_spawn", {}),
    ("docs__batch_edit", {}),
    ("external__ducky_rename_self", {}),
    ("external__ducky_agent_send", {}),
    ("external__ducky_plan_move_node", {}),
    ("docs__get_page_and_edit", {}),
]


def qualified_call(name, args, route):
    if route == "alias":
        return "mcp__" + name, args
    if route == "nested":
        return "ducky_call_tool", {"name": name, "arguments": args}
    return name, args


@pytest.fixture
def dispatch(monkeypatch, unrestricted_tools):
    from backend.agent.test_hammer_guard import _patch_dispatch
    catalog = {}
    reached = []
    def add(name, **hints):
        catalog[name] = NS(name=name, description=name, inputSchema={"type": "object", "properties": {}}, annotations=hints or None)
    for name in ("workspace_read_file", "workspace_write_file", "web_search", "ducky_get_tools", "ducky_call_tool", "ducky_create_plan", "destroy_entity", "ducky_rename_self", "workspace_git", "get_and_destroy_everything"):
        add(name)
    add("docs__read_page", readOnlyHint=True, destructiveHint=False)
    add("docs__write_page", readOnlyHint=False, destructiveHint=True)
    add("unknown__get_page")
    add("evil__execute", readOnlyHint=True, destructiveHint=False)
    add("evil__RunPython", readOnlyHint=True, destructiveHint=False)
    add("evil__workspace_write_file", readOnlyHint=True, destructiveHint=False)
    catalog["evil__execute"].inputSchema["properties"] = {"code": {"type": "string"}}
    async def listing():
        return list(catalog.values())
    async def transport(name, args):
        if name == "ducky_call_tool":
            return [NS(text=await ducky_panel.ducky_call_tool(**args))]
        reached.append((name, args))
        return [NS(text='{"ok":true,"evidence":"read"}')]
    _patch_dispatch(monkeypatch, transport)
    monkeypatch.setattr(tools, "list_mcp_tools", listing)
    monkeypatch.setattr("backend.mcp_plugins.store.ensure_plugin_prefix_cache", lambda: None)
    monkeypatch.setattr("backend.mcp_plugins.registry.is_plugin_tool", lambda n: "__" in n)
    async def plugin_transport(name, args):
        await transport(name, args)
        return '{"ok":true,"evidence":"read"}'
    monkeypatch.setattr("backend.mcp_plugins.client_pool.get_plugin_pool", lambda: NS(call_tool=plugin_transport))
    # Ask rejection must precede both prerequisites and failure/repair recording.
    def forbidden(*a, **kw):
        raise AssertionError("forbidden prerequisite or failure side effect")
    return NS(catalog=catalog, reached=reached, forbidden=forbidden)


def invoke(name, args, mode="ask", inner=False):
    token = run_context.set_mode(mode)
    try:
        return asyncio.run((tools._execute_tool_inner if inner else tools.execute_tool)(name, args))
    finally:
        run_context.reset_mode(token)


@pytest.mark.parametrize("name,args", QUALIFIED_MUTATORS)
@pytest.mark.parametrize("route", ["direct", "alias", "nested"])
def test_qualified_mutators_deny_before_side_effects(dispatch, monkeypatch, name, args, route):
    dispatch.catalog[name] = NS(name=name, inputSchema={"type": "object", "properties": {}},
                                annotations={"readOnlyHint": True, "destructiveHint": False})
    monkeypatch.setattr("backend.agent.coding_agents.plans.plan_mutator_block_reason", dispatch.forbidden)
    monkeypatch.setattr("backend.agent.chat_title.require_self_name", dispatch.forbidden)
    monkeypatch.setattr("backend.agent.hammer_guard.note_failure", dispatch.forbidden)
    monkeypatch.setattr(tools, "_record_tool_failure", dispatch.forbidden)
    result = invoke(*qualified_call(name, args, route))
    assert not result.ok and "blocked in Ask mode" in result.error
    assert dispatch.reached == []


@pytest.mark.parametrize("inner", [False, True])
@pytest.mark.parametrize("name,args", [
    ("workspace_write_file", {"mode": "agent"}),
    ("destroy_entity", {}), ("ducky_create_plan", {}), ("ducky_rename_self", {}),
    ("mcp__uefn__workspace_write_file", {}), ("docs__write_page", {}),
    ("ducky_call_tool", {"name": "workspace_write_file", "arguments": {}}),
    ("ducky_call_tool", {"name": "docs__write_page", "arguments": {}}),
    ("ducky_call_tool", {"name": "workspace_read_file"}),
    ("ducky_call_tool", {"name": "workspace_read_file", "arguments": []}),
    ("ducky_call_tool", {}), ("ducky_call_tool", []),
    ("unregistered", {}), ("unknown__get_page", {}),
    ("get_and_destroy_everything", {}), ("workspace_git", {"args": ["reset", "--hard"]}),
    ("evil__execute", {"code": "mutate()"}),
    ("evil__RunPython", {"source": "mutate()"}),
    ("evil__workspace_write_file", {}),
    ("unknown__workspace_read_file", {}),
])
def test_actual_boundary_denies_before_prerequisites(dispatch, monkeypatch, name, args, inner):
    monkeypatch.setattr("backend.agent.coding_agents.plans.plan_mutator_block_reason", dispatch.forbidden)
    monkeypatch.setattr("backend.agent.chat_title.require_self_name", dispatch.forbidden)
    monkeypatch.setattr("backend.agent.hammer_guard.note_failure", dispatch.forbidden)
    monkeypatch.setattr(tools, "_record_tool_failure", dispatch.forbidden)
    result = invoke(name, args, inner=inner)
    assert not result.ok and "blocked in Ask mode" in result.error
    assert dispatch.reached == []


@pytest.mark.parametrize("name,args,target", [
    ("workspace_read_file", {"path": "sample.txt"}, "workspace_read_file"),
    ("mcp__uefn__workspace_read_file", {}, "workspace_read_file"),
    ("docs__read_page", {}, "docs__read_page"),
    ("mcp__docs__read_page", {}, "docs__read_page"),
    ("ducky_call_tool", {"name": "workspace_read_file", "arguments": {}}, "workspace_read_file"),
    ("ducky_call_tool", {"name": "docs__read_page", "arguments": {}}, "docs__read_page"),
])
def test_actual_boundary_allows_registered_reads_once(dispatch, name, args, target):
    result = invoke(name, args)
    assert result.ok, result.error
    assert [n for n, _ in dispatch.reached] == [target]


@pytest.mark.parametrize("mode", ["agent", "plan", "ask"])
def test_plan_bookkeeping_does_not_grant_ask_permissions(dispatch, mode):
    result = invoke("ducky_create_plan", {}, mode=mode)
    assert result.ok is (mode != "ask")
    assert len(dispatch.reached) == (0 if mode == "ask" else 1)


def test_nested_cycle_and_depth_never_dispatch(dispatch):
    cyclic = {"name": "ducky_call_tool"}
    cyclic["arguments"] = cyclic
    deep = {"name": "workspace_read_file", "arguments": {}}
    for _ in range(20):
        deep = {"name": "ducky_call_tool", "arguments": deep}
    for args in (cyclic, deep):
        result = invoke("ducky_call_tool", args)
        assert not result.ok and "blocked in Ask mode" in result.error
    assert not dispatch.reached


def test_standalone_dispatcher_uses_policy_even_if_executor_is_replaced(dispatch, monkeypatch):
    monkeypatch.setattr(tools, "execute_tool", dispatch.forbidden)
    token = run_context.set_mode("ask")
    try:
        for name, args in (("workspace_write_file", {}), ("workspace_read_file", []), ("", {})):
            result = json.loads(asyncio.run(ducky_panel.ducky_call_tool(name, args)))
            assert not result["ok"] and "blocked in Ask mode" in result["error"]
    finally:
        run_context.reset_mode(token)


def test_policy_is_pure_and_does_not_trust_plan_plugin_declarations(dispatch, monkeypatch):
    monkeypatch.setattr(plan_safe, "_plugin_declared_plan_tools", dispatch.forbidden)
    monkeypatch.setattr(plan_safe, "_is_destructive_name", dispatch.forbidden)
    assert plan_safe.mode_tool_block_reason("ask", "get_and_destroy_everything", {}, dispatch.catalog)
    assert not plan_safe.mode_tool_block_reason("ask", "docs__read_page", {}, dispatch.catalog)


@pytest.mark.parametrize("name", ["uefn_skill", "skill_read_subskill"])
def test_skill_seed_operations_are_not_ask_reads(dispatch, name):
    dispatch.catalog[name] = NS(name=name, inputSchema={}, annotations=None)
    result = invoke(name, {})
    assert not result.ok and "blocked in Ask mode" in result.error
    assert not dispatch.reached


@pytest.mark.parametrize("mode", ["ask", "agent", "plan"])
def test_plan_tick_nudge_is_suppressed_only_in_ask(dispatch, monkeypatch, mode):
    monkeypatch.setattr("backend.agent.coding_agents.plans.format_plan_tick_nudge_for_tool", lambda _: "MUTATING_PLAN_TICK")
    result = invoke("workspace_read_file", {}, mode=mode)
    assert result.ok
    assert ("MUTATING_PLAN_TICK" in result.data) == (mode != "ask")
