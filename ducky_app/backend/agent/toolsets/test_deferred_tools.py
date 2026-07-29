"""Cursor-style deferred tools: floor select_tools + get/call helpers."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from backend.agent.run_context import reset_plan_only, set_plan_only
from backend.agent.toolsets import CORE_TOOLS, effective_tool_name, select_tools
from backend.agent.toolsets.tool_index import build_tool_index_text, truncate_desc


class _T:
    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description
        self.inputSchema = {"type": "object", "properties": {"x": {"type": "string"}}}


def test_floor_core_is_tiny():
    assert "ducky_get_tools" in CORE_TOOLS
    assert "ducky_call_tool" in CORE_TOOLS
    assert "workspace_list_verse_errors" in CORE_TOOLS
    assert "wire_verse_device_ref" not in CORE_TOOLS
    assert len(CORE_TOOLS) <= 12


def test_select_tools_ignores_intent_noise():
    all_tools = [
        _T(n)
        for n in list(CORE_TOOLS)
        + ["wire_verse_device_ref", "blender_status", "discord_list_channels"]
    ]
    sel = {t.name for t in select_tools(all_tools, "wire blender discord verse", plan_only=False)}
    assert sel <= CORE_TOOLS
    assert "wire_verse_device_ref" not in sel


def test_truncate_desc():
    long = "a" * 300
    out = truncate_desc(long, 50)
    assert out.endswith("... [truncated]")
    assert len(out) <= 50


def test_tool_index_groups_and_excludes_meta():
    tools = [
        _T("ducky_get_tools", "meta"),
        _T("ducky_call_tool", "meta"),
        _T("blender_status", "Blender socket status"),
        _T("foo__list", "nested list"),
        _T("workspace_read_file", "read a file"),
    ]
    text = build_tool_index_text(tools)
    catalog = "\n".join(line for line in text.splitlines() if line.startswith("- "))
    assert "`ducky_get_tools`" not in catalog
    assert "`ducky_call_tool`" not in catalog
    assert "`blender_status`" in catalog
    assert "### desktop" in text
    assert "### nested:foo" in text


def test_tool_index_local_desc_max_shorter():
    tools = [_T("blender_status", "x" * 200)]
    fat = build_tool_index_text(tools, desc_max=200)
    slim = build_tool_index_text(tools, desc_max=70)
    assert len(slim) < len(fat)
    assert "… [truncated]" in slim or "... [truncated]" in slim


def test_effective_tool_name_unwraps_call():
    assert effective_tool_name("workspace_read_file", {"path": "a"}) == "workspace_read_file"
    assert (
        effective_tool_name("ducky_call_tool", {"name": "blender_status", "arguments": {}})
        == "blender_status"
    )


def test_ducky_get_tools_name_and_pattern(monkeypatch):
    from backend.tools import ducky_panel
    import backend.agent.tools as tools_mod

    tools = [
        _T("alpha_tool", "Alpha does things"),
        _T("beta_search", "Search beta docs"),
    ]

    async def _list():
        return tools

    monkeypatch.setattr(tools_mod, "list_mcp_tools", _list)

    one = json.loads(asyncio.run(ducky_panel.ducky_get_tools(name="alpha_tool")))
    assert one["name"] == "alpha_tool"
    assert "inputSchema" in one

    pat = json.loads(asyncio.run(ducky_panel.ducky_get_tools(pattern="beta")))
    assert pat["count"] >= 1
    assert any(m["name"] == "beta_search" for m in pat["matches"])


def test_ducky_call_tool_plan_blocks_mutator(monkeypatch):
    from backend.tools import ducky_panel
    import backend.agent.tools as tools_mod

    called: list[str] = []

    async def _exec(name, arguments=None, **_kw):
        called.append(name)
        return SimpleNamespace(ok=True, data="{}", error=None, hint="")

    monkeypatch.setattr(tools_mod, "execute_tool", _exec)
    token = set_plan_only(True)
    try:
        out = json.loads(
            asyncio.run(ducky_panel.ducky_call_tool(name="spawn_actor", arguments={"asset": "x"}))
        )
        assert out.get("ok") is False
        assert called == []
    finally:
        reset_plan_only(token)


def test_ducky_call_tool_dispatches(monkeypatch):
    from backend.tools import ducky_panel
    import backend.agent.tools as tools_mod

    async def _exec(name, arguments=None, **_kw):
        assert name == "ping"
        return SimpleNamespace(ok=True, data='{"ok":true}', error=None, hint="")

    monkeypatch.setattr(tools_mod, "execute_tool", _exec)
    token = set_plan_only(False)
    try:
        raw = asyncio.run(ducky_panel.ducky_call_tool(name="ping", arguments={}))
        assert "ok" in raw
    finally:
        reset_plan_only(token)
