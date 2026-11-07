"""Call time limits: Epic UEFN calls (unreal__*) have none; they wait while UEFN works."""

from __future__ import annotations

from backend.bridge import call_limits


def test_unreal_calls_have_no_limit_and_the_rest_keep_theirs() -> None:
    lim = call_limits.call_limit
    assert lim("unreal__call_tool", "unreal__call_tool") is None
    assert lim("unreal__list_toolsets", "unreal__list_toolsets") is None
    assert lim("ducky_call_tool", "unreal__call_tool") is None
    assert lim("workspace_read_file", "workspace_read_file") == 30.0
    assert lim("skill_search", "skill_search") == 60.0
    assert lim("ducky_call_tool", "roblox_call") == 900.0
    assert lim("ducky_call_tool", "ducky_ask_user") is None
    assert lim("ducky_ask_user", "ducky_ask_user") is None
    assert lim("spawn_layout", "spawn_layout") is None


def test_timed_out_message_for_other_tools() -> None:
    assert "did not answer within 30 s" in call_limits.timed_out_message("workspace_read_file", 30)
