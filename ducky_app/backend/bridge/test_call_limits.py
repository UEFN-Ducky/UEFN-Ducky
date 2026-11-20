"""Call time limits: Epic UEFN calls (unreal__*) only have a backstop past their own deadline."""

from __future__ import annotations

from backend.bridge import call_limits


def test_unreal_calls_get_a_backstop_and_the_rest_keep_theirs() -> None:
    """Nov 19 2026: two unreal__ calls sat past 20 min before they reached their own deadline."""
    lim = call_limits.call_limit
    backstop = call_limits.EPIC_HEAVY_LIMIT_S + call_limits.EPIC_BACKSTOP_EXTRA_S
    assert backstop == 450.0
    assert lim("unreal__call_tool", "unreal__call_tool") == backstop
    assert lim("unreal__list_toolsets", "unreal__list_toolsets") == backstop
    assert lim("ducky_call_tool", "unreal__call_tool") == backstop
    # Past the heavy deadline plus the stuck check's grace, so client_pool always answers first.
    from backend.bridge import stuck_watch

    assert backstop > call_limits.EPIC_HEAVY_LIMIT_S + stuck_watch.EPIC_GRACE_S
    assert lim("workspace_read_file", "workspace_read_file") == 30.0
    assert lim("skill_search", "skill_search") == 60.0
    assert lim("ducky_call_tool", "roblox_call") == 900.0
    assert lim("ducky_call_tool", "ducky_ask_user") is None
    assert lim("ducky_ask_user", "ducky_ask_user") is None
    assert lim("spawn_layout", "spawn_layout") is None


def test_timed_out_message_for_other_tools() -> None:
    assert "did not answer within 30 s" in call_limits.timed_out_message("workspace_read_file", 30)


def test_a_backstopped_epic_call_says_what_holds_it(monkeypatch) -> None:
    monkeypatch.setattr(call_limits, "blocking_popup", lambda: None)
    monkeypatch.setattr(call_limits, "_command_in_window", lambda: None)
    monkeypatch.setattr(call_limits, "_editor_answers", lambda: True)
    msg = call_limits.timed_out_message("unreal__call_tool", 450)
    assert "unreal__call_tool did not answer in 450 s" in msg and "Check the result" in msg


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
    monkeypatch.setattr(call_limits, "_command_in_window", lambda: None)
    monkeypatch.setattr(call_limits, "_editor_answers", lambda: False)
    busy = call_limits.timed_out_message("import_asset", 300)
    assert "import_asset did not answer in 300 s and UEFN's game thread is busy" in busy
    assert "its work goes on" in busy and "was stopped" not in busy and "once more" not in busy

    monkeypatch.setattr(call_limits, "blocking_popup", lambda: {"hwnd": 0x1A2B, "title": "Save Content", "blocking": True})
    popup = call_limits.timed_out_message("execute_python", 300)
    assert "held by a UEFN popup" in popup and "uefn_popup_press" in popup
