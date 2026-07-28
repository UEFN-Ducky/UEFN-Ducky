"""Plan-safe tool predicate + floor-only select_tools (Cursor-style dispatcher)."""

from __future__ import annotations

from backend.agent.toolsets import CORE_TOOLS, PLAN_TOOLS, select_tools
from backend.agent.toolsets.plan_safe import is_plan_safe_tool, tool_leaf_name


class _T:
    def __init__(self, name: str) -> None:
        self.name = name


DISCORD_TOOLS = [
    "discord_list_bots",
    "discord_list_channels",
    "discord_read_channel",
    "discord_list_roles",
    "discord_list_members",
    "discord_send_message",
    "discord_kick_member",
    "discord_ban_member",
    "discord_delete_channel",
]


def test_leaf_name_strips_nested_prefix():
    assert tool_leaf_name("drive__list_files") == "list_files"
    assert tool_leaf_name("discord_list_channels") == "discord_list_channels"


def test_discord_reads_plan_safe_mutators_not():
    for name in (
        "discord_list_bots",
        "discord_list_channels",
        "discord_read_channel",
        "discord_list_roles",
        "discord_list_members",
    ):
        assert is_plan_safe_tool(name), name
    for name in (
        "discord_send_message",
        "discord_kick_member",
        "discord_ban_member",
        "discord_delete_channel",
        "discord_create_invite",
    ):
        assert not is_plan_safe_tool(name), name


def test_nested_mcp_stem_filter():
    assert is_plan_safe_tool("acme__list_bars")
    assert is_plan_safe_tool("acme__get_status")
    assert is_plan_safe_tool("acme__search_docs")
    assert not is_plan_safe_tool("acme__delete_bars")
    assert not is_plan_safe_tool("acme__send_message")
    assert not is_plan_safe_tool("acme__create_item")


def test_select_tools_agent_is_tiny_floor():
    extras = DISCORD_TOOLS + ["spawn_actor", "wire_verse_device_ref", "project_memory_save"]
    all_tools = [_T(n) for n in sorted(CORE_TOOLS | PLAN_TOOLS | set(extras))]
    sel = {t.name for t in select_tools(all_tools, "discord wire spawn memory", plan_only=False)}
    assert sel == (CORE_TOOLS & {t.name for t in all_tools})
    assert "ducky_call_tool" in sel
    assert "ducky_get_tools" in sel
    assert "workspace_list_verse_errors" in sel
    assert "discord_list_channels" not in sel
    assert "spawn_actor" not in sel


def test_select_tools_plan_is_plan_floor():
    extras = DISCORD_TOOLS + ["ducky_store_search", "ducky_create_plan"]
    all_tools = [_T(n) for n in sorted(CORE_TOOLS | PLAN_TOOLS | set(extras))]
    plan_sel = {t.name for t in select_tools(all_tools, "check discord channels", plan_only=True)}
    assert plan_sel == (PLAN_TOOLS & {t.name for t in all_tools})
    assert "ducky_call_tool" in plan_sel
    assert "discord_list_channels" not in plan_sel
    assert "ducky_store_search" not in plan_sel


def test_plan_tools_declared_override(monkeypatch):
    import backend.uefn_plugins.host as host

    monkeypatch.setattr(host, "plugin_plan_tools", lambda: frozenset({"custom_weird_read"}))
    monkeypatch.setattr(host, "plugin_destructive_desktop_tools", lambda: frozenset())
    monkeypatch.setattr(
        "backend.agent.toolsets.mcp_plugins.nested_mcp_plan_tool_names",
        lambda: frozenset(),
    )
    assert is_plan_safe_tool("custom_weird_read")


def test_plugin_surface_reads_plan_safe():
    assert is_plan_safe_tool("get_ik_rig_info")
    assert is_plan_safe_tool("get_retarget_preset")
    assert is_plan_safe_tool("find_clear_area")
    assert is_plan_safe_tool("metahuman_list")
    assert is_plan_safe_tool("landscape_list")
    assert is_plan_safe_tool("ducktactoe_state")
    assert is_plan_safe_tool("validate_uefn_asset")
    assert is_plan_safe_tool("blender_get_viewport_screenshot")


def test_meshy_plan_safe_reads_only():
    assert not is_plan_safe_tool("meshy_discover_download")
    assert not is_plan_safe_tool("meshy_text_to_3d_preview")
    assert not is_plan_safe_tool("meshy_text_to_3d")
    assert not is_plan_safe_tool("meshy_download")
    assert not is_plan_safe_tool("meshy_rig")
    assert is_plan_safe_tool("meshy_discover_search")
    assert is_plan_safe_tool("meshy_discover_get")
