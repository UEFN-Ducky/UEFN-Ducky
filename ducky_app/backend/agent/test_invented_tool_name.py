"""Invented MCP names map onto one registered tool, or onto none."""

from backend.agent.tools import resolve_invented_tool_name

_KNOWN = {"ducky_get_status", "blender_get_scene_info", "blender__read_image", "other_get_scene_info"}


def test_uefn_prefix_is_the_ducky_tool_itself() -> None:
    assert resolve_invented_tool_name("mcp__uefn__ducky_get_status", _KNOWN) == "ducky_get_status"


def test_mcp_dunder_prefix_becomes_flat_name() -> None:
    assert resolve_invented_tool_name("mcp__blender__read_image", _KNOWN) == "blender__read_image"


def test_hyphenated_tail_matches_one_registered_name() -> None:
    known = {"ducky_get_status", "blender_get_scene_info"}
    assert (
        resolve_invented_tool_name("mcp-blender-mcp-get_scene_info", known)
        == "blender_get_scene_info"
    )


def test_two_tails_is_no_match() -> None:
    assert resolve_invented_tool_name("mcp-blender-mcp-get_scene_info", _KNOWN) is None


def test_a_guessed_ducky_name_is_the_tool_it_stands_for() -> None:
    """Nov 10 2026: ducky_memory_get 14 times and eight more guessed names in one day."""
    known = {"project_memory_get", "project_memory_list", "workspace_read_file", "workspace_find", "skill_read_subskill"}
    assert resolve_invented_tool_name("ducky_memory_get", known) == "project_memory_get"
    assert resolve_invented_tool_name("ducky_memory_search", known) == "project_memory_list"
    assert resolve_invented_tool_name("ducky_memory_get_all", known) == "project_memory_list"
    assert resolve_invented_tool_name("ducky_read_file", known) == "workspace_read_file"
    assert resolve_invented_tool_name("ducky_read", known) == "workspace_read_file"
    assert resolve_invented_tool_name("ducky_list_files", known) == "workspace_find"
    assert resolve_invented_tool_name("ducky_skill_read", known) == "skill_read_subskill"
    assert resolve_invented_tool_name("ducky_read_subskill", known) == "skill_read_subskill"
    # Only when the real tool is there, and never over a tool that has the guessed name.
    assert resolve_invented_tool_name("ducky_memory_get", {"workspace_find"}) is None
    assert resolve_invented_tool_name("ducky_read", known | {"ducky_read"}) is None


def test_missing_and_junk_names_do_not_guess() -> None:
    assert resolve_invented_tool_name("mcp__uefn__missing", _KNOWN) is None
    assert resolve_invented_tool_name("computer", _KNOWN) is None
    assert resolve_invented_tool_name("computer_use", _KNOWN) is None
    assert resolve_invented_tool_name("vision_analyze", _KNOWN) is None
    assert resolve_invented_tool_name("blender_get_scene_info", _KNOWN) is None
