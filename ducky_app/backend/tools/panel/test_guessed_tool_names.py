"""A tool name the agent guessed costs one answer, not a failed call and a lost turn
(Nov 10 2026: ducky_memory_get x14, ducky_read_file, ducky_list_files ... in one day)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import backend.agent.tools as tools_mod
from backend.agent.run_context import reset_plan_only, set_plan_only
from backend.tools.panel import ducky_panel
from frontend import usage_stats


@pytest.fixture(autouse=True)
def _agent_mode():
    token = set_plan_only(False)
    yield
    reset_plan_only(token)


def test_a_guessed_name_that_ran_as_the_real_tool_says_which(monkeypatch) -> None:
    async def _exec(name, arguments=None, **_kw):
        # execute_tool resolves the guessed name and reports the tool that ran.
        return SimpleNamespace(ok=True, tool="project_memory_get", data='{"name":"style","content":"x"}', error=None, hint="")

    monkeypatch.setattr(tools_mod, "execute_tool", _exec)
    out = json.loads(asyncio.run(ducky_panel.ducky_call_tool(name="ducky_memory_get", arguments={"name": "style"})))
    assert out["content"] == "x"
    assert out["tool_note"] == "ducky_memory_get is not a tool: this ran project_memory_get. Use that name."


def test_a_real_tool_gets_no_note(monkeypatch) -> None:
    async def _exec(name, arguments=None, **_kw):
        return SimpleNamespace(ok=True, tool=name, data='{"ok":true}', error=None, hint="")

    monkeypatch.setattr(tools_mod, "execute_tool", _exec)
    assert json.loads(asyncio.run(ducky_panel.ducky_call_tool(name="ping", arguments={}))) == {"ok": True}


def test_an_unknown_name_lists_the_closest_tools_and_counts_as_not_found(monkeypatch) -> None:
    async def _exec(name, arguments=None, **_kw):
        return SimpleNamespace(ok=False, tool=name, data=None, error=f"Unknown tool: {name}. Call ducky_call_tool ...", hint="h")

    async def _tools():
        return [SimpleNamespace(name=n, description=d, inputSchema={}) for n, d in (
            ("project_memory_get", "Pull one memory entry"), ("project_memory_list", "Index of a project's memory"),
            ("spawn_actor", "Spawn an actor"),
        )]

    monkeypatch.setattr(tools_mod, "execute_tool", _exec)
    monkeypatch.setattr(tools_mod, "list_mcp_tools", _tools)
    monkeypatch.setattr(
        ducky_panel, "_ranked_tool_names",
        lambda tools, by_name, text, limit, **_kw: [n for n in by_name if "memory" in n and "memory" in text][:limit],
    )
    raw = asyncio.run(ducky_panel.ducky_call_tool(name="ducky_memory_fetch", arguments={"name": "style"}))
    out = json.loads(raw)
    assert out["ok"] is False and out["fail"] == "not_found"
    assert out["close_matches"] == ["project_memory_get", "project_memory_list"]
    assert "Closest: project_memory_get, project_memory_list" in out["hint"]
    # The usage count reads the same word, so a guessed name is told apart from a broken tool.
    assert usage_stats.fail_of(raw) == "not_found"


def test_an_unknown_name_with_no_close_tool_keeps_its_own_hint(monkeypatch) -> None:
    async def _exec(name, arguments=None, **_kw):
        return SimpleNamespace(ok=False, tool=name, data=None, error=f"Unknown tool: {name}.", hint="search with ducky_get_tools")

    async def _tools():
        return []

    monkeypatch.setattr(tools_mod, "execute_tool", _exec)
    monkeypatch.setattr(tools_mod, "list_mcp_tools", _tools)
    monkeypatch.setattr(ducky_panel, "_ranked_tool_names", lambda *a, **k: [])
    out = json.loads(asyncio.run(ducky_panel.ducky_call_tool(name="zzz_nope", arguments={})))
    assert out["fail"] == "not_found" and out["hint"] == "search with ducky_get_tools" and "close_matches" not in out
