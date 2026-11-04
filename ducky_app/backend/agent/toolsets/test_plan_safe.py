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


def test_plan_mode_blocks_reload_allows_changeset_reads():
    assert not is_plan_safe_tool("reload_listener")
    assert is_plan_safe_tool("changeset_list")
    assert is_plan_safe_tool("changeset_contents")
    assert is_plan_safe_tool("changeset_export")
    assert not is_plan_safe_tool("changeset_revert")


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


def test_ask_answers_questions_about_the_level_and_ducky_but_changes_nothing():
    from backend.agent.toolsets.plan_safe import mode_tool_block_reason

    reads = ["ducky_get_status", "get_all_actors", "get_actor_properties", "search_assets",
             "get_level_info", "ducky_terminal_read_output", "ducky_read_chat", "get_verse_api"]
    writes = ["set_actor_properties", "save_current_level", "select_actors", "spawn_actor",
              "ducky_terminal_run", "ducky_send_chat_message", "execute_python", "save_asset"]
    catalog = {name: _T(name) for name in reads + writes}
    for mode in ("ask", "plan"):
        for name in reads:
            assert mode_tool_block_reason(mode, name, {}, catalog) == "", (mode, name)
        for name in writes:
            assert mode_tool_block_reason(mode, name, {}, catalog), (mode, name)


def test_real_catalog_reads_pass_in_ask_and_plan():
    """The Oct 24 refusals, replayed against the real registry the tool server lists."""
    import asyncio

    from backend.server import mcp
    import backend.tools  # noqa: F401 — registers the core tools
    from backend.agent.toolsets.plan_safe import mode_tool_block_reason

    catalog = {t.name: t for t in asyncio.run(mcp.list_tools())}
    calls = [("ducky_get_plan", {"chat_id": "b47c8c50-efa4-4371-a191-f80e323a4a9f"}), ("ducky_list_plans", {}),
             ("workspace_search", {"pattern": "7df73ab45675", "output_mode": "files"}),
             ("workspace_search", {"pattern": "7df73ab45675", "path": "ducky_app"}),
             ("workspace_read_file", {"path": "README.md"})]
    for mode in ("ask", "plan"):
        for name, args in calls:
            for spelling in (name, "mcp__uefn__" + name):
                assert mode_tool_block_reason(mode, spelling, args, catalog) == "", (mode, spelling)
            nested = {"name": name, "arguments": args}
            assert mode_tool_block_reason(mode, "ducky_call_tool", nested, catalog) == "", (mode, name)
        for name in ("workspace_write_file", "ducky_terminal_run", "execute_python"):
            for spelling in (name, "mcp__uefn__" + name):
                assert mode_tool_block_reason(mode, spelling, {}, catalog), (mode, spelling)
    assert mode_tool_block_reason("ask", "ducky_plan_update_node", {}, catalog)


def test_permission_prompt_answers_from_the_mode_policy():
    """Claude Code (plan permission mode) asks ducky_permission_prompt before each MCP call."""
    import json

    from backend.agent.toolsets.plan_safe import mode_permission_answer

    catalog = {n: _T(n) for n in ("ducky_permission_prompt", "ducky_get_plan", "workspace_search",
                                  "workspace_read_file", "workspace_write_file", "ducky_plan_update_node",
                                  "ducky_terminal_run", "execute_python", "ducky_call_tool")}

    def answer(mode, target, args=None):
        raw = mode_permission_answer(mode, "ducky_permission_prompt", {"tool_name": target, "input": args or {}}, catalog)
        return json.loads(raw)

    for mode in ("ask", "plan"):
        for name in ("ducky_get_plan", "workspace_search", "workspace_read_file"):
            for spelling in (name, "mcp__uefn__" + name):
                assert answer(mode, spelling, {"pattern": "x"}) == {"behavior": "allow", "updatedInput": {"pattern": "x"}}
        assert answer(mode, "Read")["behavior"] == "allow"
        for name in ("workspace_write_file", "ducky_terminal_run", "execute_python", "ducky_permission_prompt", "Bash", "nope"):
            for spelling in (name, "mcp__uefn__" + name):
                denied = answer(mode, spelling)
                assert denied["behavior"] == "deny" and f"{mode.title()} mode" in denied["message"], (mode, spelling)
        nested = answer(mode, "mcp__uefn__ducky_call_tool", {"name": "workspace_write_file", "arguments": {}})
        assert nested["behavior"] == "deny"
    assert answer("ask", "mcp__uefn__ducky_plan_update_node")["behavior"] == "deny"
    assert answer("plan", "mcp__uefn__ducky_plan_update_node")["behavior"] == "allow"
    assert mode_permission_answer("agent", "ducky_permission_prompt", {"tool_name": "Bash"}, catalog) is None
    assert mode_permission_answer("ask", "ducky_get_plan", {}, catalog) is None


def test_read_only_tools_never_wait_for_a_plan_tick(monkeypatch):
    """Reading a long tool result, a chat or a terminal is never gated on ticking a plan step."""
    from backend.agent.coding_agents import plans

    monkeypatch.setattr(plans, "load_active_plan", lambda: {"nodes": []})
    monkeypatch.setattr(plans, "next_tick_dict", lambda _plan: {"n": 1, "id": "diagnose", "status": "pending",
                                                                "content": "Diagnose", "call": "ducky_plan_update_node(...)"})
    for name in ("ducky_read_tool_spill", "ducky_read_chat", "ducky_terminal_read_output", "get_editor_log"):
        assert is_plan_safe_tool(name), name
        assert plans.plan_mutator_block_reason(name) is None, name
    assert plans.plan_mutator_block_reason("save_current_level") is None  # nothing blocks (2026-11-03)
