"""Call time limits: Epic UEFN calls (unreal__*) only have a backstop past their own deadline."""

from __future__ import annotations

from backend.bridge import call_limits


def test_unreal_calls_get_a_backstop_and_the_rest_keep_theirs(monkeypatch) -> None:
    """Nov 19 2026: two unreal__ calls sat past 20 min before they reached their own deadline."""
    from backend.mcp_plugins import epic

    monkeypatch.setitem(epic._probe_cache, "result", None)  # no probe yet: UEFN
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


def test_unreal_editor_calls_keep_no_limit(monkeypatch) -> None:
    from backend.mcp_plugins import epic

    monkeypatch.setitem(epic._probe_cache, "result", {"epic_mcp_editor": epic.EDITOR_UNREAL})
    assert call_limits.call_limit("unreal__call_tool", "unreal__call_tool") is None
    monkeypatch.setitem(epic._probe_cache, "result", {"epic_mcp_editor": epic.EDITOR_UEFN})
    assert call_limits.call_limit("unreal__call_tool", "unreal__call_tool") == 450.0


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


def test_stall_reason_never_waits_on_a_held_listener_lock(monkeypatch) -> None:
    """Nov 20 2026: 5 unreal__ calls ran past 20 min. At the deadline the editor check pinged
    UEFN, and the ping queued behind another command's listener lock with no limit."""
    import threading

    from backend.bridge import client

    monkeypatch.setattr(call_limits, "blocking_popup", lambda: None)
    monkeypatch.setattr(call_limits, "_command_in_window", lambda: None)
    monkeypatch.setattr(call_limits, "_PING_LOCK_WAIT_S", 0.2, raising=False)
    # The POST itself answers at once: only the lock can hold the ping.
    monkeypatch.setattr(client, "_post_json_locked", lambda *a, **k: {"success": True, "result": {}})
    client._invalidate_cache()
    got: list = []
    client._listener_lock.acquire()
    try:
        worker = threading.Thread(target=lambda: got.append(call_limits.stall_reason("unreal__call_tool", 120)),
                                  daemon=True)
        worker.start()
        worker.join(timeout=2.0)
        assert not worker.is_alive(), "stall_reason waited on the listener lock"
    finally:
        client._listener_lock.release()
    assert got[0]["reason"] == "editor_busy"


def test_a_hung_editor_check_still_answers_within_its_budget(monkeypatch) -> None:
    import time

    monkeypatch.setattr(call_limits, "stall_reason", lambda op, limit: time.sleep(3) or {"reason": "popup"})
    began = time.monotonic()
    why = call_limits.stall_reason_bounded("unreal__call_tool", 120, budget=0.3)
    assert time.monotonic() - began < 1.0
    assert why["reason"] == "no_answer" and "uefn_popups" in why["message"] and "Check the result" in why["message"]


def test_late_messages_send_the_agent_to_uefn_popups(monkeypatch) -> None:
    monkeypatch.setattr(call_limits, "blocking_popup", lambda: None)
    monkeypatch.setattr(call_limits, "_command_in_window", lambda: None)
    monkeypatch.setattr(call_limits, "_editor_answers", lambda: True)
    late = call_limits.stall_reason("unreal__call_tool", 120)["message"]
    assert "uefn_popups" in late and "ONCE" in late and "Check the result" in late
    monkeypatch.setattr(call_limits, "_editor_answers", lambda: False)
    busy = call_limits.stall_reason("unreal__call_tool", 120)["message"]
    assert "uefn_popups" in busy and "once" not in busy.lower()  # its work goes on: never sent again
