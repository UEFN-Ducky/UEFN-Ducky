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


def test_long_listener_tools_answer_at_the_heavy_editor_deadline() -> None:
    """Nov 11 2026: import_asset 10, execute_python 6 and take_high_res_screenshot 2 calls sat
    past 5 minutes with no word to the agent."""
    lim = call_limits.call_limit
    for tool in ("import_asset", "execute_python", "take_high_res_screenshot"):
        assert lim(tool, tool) == call_limits.EPIC_HEAVY_LIMIT_S == 300.0
        assert lim("ducky_call_tool", tool) == 300.0  # not the 900 s plugin limit
    assert lim("spawn_actor", "spawn_actor") is None  # quick listener tools keep the bridge's own timeout


def test_a_late_editor_job_says_what_holds_it_and_never_to_send_it_again(monkeypatch) -> None:
    monkeypatch.setattr(call_limits, "blocking_popup", lambda: None)
    monkeypatch.setattr(call_limits, "_editor_answers", lambda: False)
    busy = call_limits.timed_out_message("import_asset", 300)
    assert "import_asset did not answer in 300 s and UEFN's game thread is busy" in busy
    assert "its work goes on" in busy and "was stopped" not in busy and "once more" not in busy

    monkeypatch.setattr(call_limits, "blocking_popup", lambda: {"hwnd": 0x1A2B, "title": "Save Content", "blocking": True})
    popup = call_limits.timed_out_message("execute_python", 300)
    assert "held by a UEFN popup" in popup and "uefn_popup_press" in popup
