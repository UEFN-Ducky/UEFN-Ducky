"""Agent-to-agent messages never cancel the chat they reach: a busy chat holds them."""

from __future__ import annotations

import time
from types import SimpleNamespace

from frontend.ui_web.agent_turns import pending, routing, state
from frontend.ui_web.agent_turns.testing import patch_turns


def test_a_busy_chat_holds_agent_messages_and_runs_them_after_its_turn(monkeypatch) -> None:
    cancels: list[object] = []
    patch_turns(monkeypatch, "load_conversation", lambda cid, **kw: SimpleNamespace(id=cid))
    patch_turns(monkeypatch, "is_agent_running", lambda cid: True)
    patch_turns(monkeypatch, "cancel_agent", lambda cid=None, **kw: cancels.append(cid))
    patch_turns(monkeypatch, "_pending_agent_messages", {})

    assert routing.run_message("c1", "report 1", "agent", "", queue_if_busy=True, _local=True) == "queued"
    assert routing.run_message("c1", "report 2", "agent", "", queue_if_busy=True, _local=True) == "queued"
    assert cancels == []  # the coordinator's turn keeps running
    assert state._pending_agent_messages["c1"] == ["report 1", "report 2"]

    started: list[tuple[str, str, dict]] = []
    patch_turns(monkeypatch, "wait_for_idle", lambda cid, timeout=None: True)
    patch_turns(monkeypatch, "run_message", lambda cid, text, mode, model, **kw: started.append((cid, text, kw)) or "run")
    pending._deliver_pending_agent_messages("c1")
    deadline = time.time() + 5
    while not started and time.time() < deadline:
        time.sleep(0.02)
    assert started and started[0][:2] == ("c1", "report 1\n\nreport 2")
    assert started[0][2].get("queue_if_busy") is True
    assert "c1" not in state._pending_agent_messages


def test_a_person_pressing_stop_drops_held_agent_messages(monkeypatch) -> None:
    patch_turns(monkeypatch, "_pending_agent_messages", {"c2": ["held"]})
    state.cancel_agent("c2")
    assert "c2" not in state._pending_agent_messages


def test_a_turn_that_replaces_a_running_one_is_not_a_stop(monkeypatch) -> None:
    # Oct 12 2026: a recycle's fresh-session message replaced the member's running turn; the
    # lead was told "stopped by the user" and stood its team down.
    from backend.agent import a2a_broker as broker
    stops: list[str] = []
    monkeypatch.setattr(broker, "on_agent_cancelled_by_user", stops.append)
    patch_turns(monkeypatch, "_pending_agent_messages", {})
    state.cancel_agent("c3", by_user=False)
    assert stops == []
    state.cancel_agent("c3")  # the person's Stop
    assert stops == ["c3"]


def test_terminal_quota_tap_retains_pending_until_cooldown(monkeypatch):
    from backend.agent import a2a_broker as broker
    monkeypatch.setattr(broker, "_cooldowns", {}, raising=False)
    monkeypatch.setattr(broker, "_stopped", set(), raising=False)
    monkeypatch.setattr(broker, "_uncertain_chats", set(), raising=False)
    monkeypatch.setattr(broker, "_retry_queue_later", lambda cid: None)
    patch_turns(monkeypatch, "_pending_agent_messages", {"limited": ["queued work"]})
    calls = []
    patch_turns(monkeypatch, "_deliver_pending_agent_messages", lambda cid: calls.append(cid))
    tap = pending._make_broker_tap(lambda event: None, "limited")
    event = {"type": "agent_stopped", "conv_id": "limited", "reason": "error",
             "detail": '{"error":{"code":"insufficient_quota"}}'}
    tap(event)
    tap(event)
    assert calls == []
    assert state._pending_agent_messages == {"limited": ["queued work"]}


def test_held_messages_are_handed_over_once(monkeypatch) -> None:
    patch_turns(monkeypatch, "_pending_agent_messages", {"c3": ["one", "two"]})
    assert pending.take_pending_agent_messages("c3") == ["one", "two"]
    assert pending.take_pending_agent_messages("c3") == []


def test_a_person_send_the_turn_took_as_a_steer_does_not_run_again(monkeypatch) -> None:
    from backend.agent import a2a_broker as broker
    from frontend.ui_web import steer

    monkeypatch.setattr(broker, "_retry_queue_later", lambda cid: None)
    monkeypatch.setattr(steer, "_inbox", {})
    patch_turns(monkeypatch, "is_agent_running", lambda cid: True)
    patch_turns(monkeypatch, "cancel_agent", lambda cid=None, **kw: _never_cancel())
    patch_turns(monkeypatch, "_pending_agent_messages", {})
    patch_turns(monkeypatch, "_pending_steered", {})
    assert routing.run_message("p1", "taken", "agent", "", _local=True) == "queued"
    assert routing.run_message("p1", "also taken", "agent", "", _local=True) == "queued"
    lease = steer.take("p1")
    steer.ack("p1", lease["lease_id"])  # the turn took both ...
    steer.post("p1", "late", "later")  # ... and this one arrived after
    state._pending_agent_messages["p1"].append("agent report")
    state._pending_steered["p1"]["late"] = "later"
    state._pending_agent_messages["p1"].append("later")
    pending._drop_steered_pending("p1")
    assert state._pending_agent_messages["p1"] == ["agent report", "later"]


def test_a_person_send_with_attachments_waits_as_its_own_turn(monkeypatch) -> None:
    from backend.agent import a2a_broker as broker

    monkeypatch.setattr(broker, "_retry_queue_later", lambda cid: None)
    patch_turns(monkeypatch, "is_agent_running", lambda cid: True)
    patch_turns(monkeypatch, "_pending_agent_messages", {})
    patch_turns(monkeypatch, "_pending_person_sends", {})
    att = [{"name": "shot.png", "path": "x"}]
    assert routing.run_message("p2", "look", "ask", "", attachments=att, _local=True) == "queued"
    assert state._pending_person_sends["p2"] == [{"text": "look", "mode": "ask", "attachments": att}]
    assert "p2" not in state._pending_agent_messages

    started: list[tuple[str, dict]] = []
    patch_turns(monkeypatch, "wait_for_idle", lambda cid, timeout=None: True)
    monkeypatch.setattr(broker, "automatic_work_blocked", lambda cid, queued=False: False)
    patch_turns(monkeypatch, "run_message", lambda cid, text, mode, model, **kw: started.append((text, kw)) or "run")
    pending._deliver_pending_agent_messages("p2")
    deadline = time.time() + 5
    while not started and time.time() < deadline:
        time.sleep(0.02)
    assert started and started[0][0] == "look" and started[0][1]["attachments"] == att


def _never_cancel() -> None:
    raise AssertionError("a send must never cancel the running turn")


def test_an_agent_waiting_on_a_busy_chat_queues_instead_of_replacing_its_turn(monkeypatch) -> None:
    # Oct 21 2026: a lead's sends cancelled its busy member's turn four times in a row.
    patch_turns(monkeypatch, "is_agent_running", lambda cid: True)
    sent: list[dict] = []
    patch_turns(monkeypatch, "run_message", lambda cid, text, mode, model, **kw: sent.append(kw) or "queued")
    out = routing.run_message_and_wait("member", "next task", queue_if_busy=True, _local=True)
    assert out["status"] == "queued" and out["error"] == pending.BUSY_QUEUED
    assert sent[0]["queue_if_busy"] is True and not sent[0].get("force")


def test_the_bridge_asks_the_panel_which_chats_run_and_stops_them_there(monkeypatch, tmp_path) -> None:
    # Oct 21 2026: from the MCP bridge a Codex lead saw its busy member as idle and sent all its
    # parallel work into it, and ducky_agent_stop stopped nothing: the agents run in the panel.
    from backend.panel import rpc
    from frontend.ui_web import panel_httpd

    monkeypatch.setattr(panel_httpd, "_server", None)
    monkeypatch.setattr(panel_httpd, "_root", None)
    monkeypatch.setattr(panel_httpd, "PANEL_UI_HTTP_PORT", 0)
    monkeypatch.setattr(panel_httpd, "verify_panel_dist", lambda _root: None)
    panel_httpd.start_panel_ui_server(tmp_path)
    server = panel_httpd._server
    monkeypatch.setattr(rpc, "_RPC_URL", f"http://127.0.0.1:{server.server_address[1]}/__panel_rpc")
    asked: list[str] = []

    def panel_state(cancel="", by_user=True):
        asked.append(cancel)
        return {"running": [] if cancel else ["member"], "parents": {"member": "lead"}}

    patch_turns(monkeypatch, "run_state", panel_state)
    patch_turns(monkeypatch, "_panel_runs_cache", (float("-inf"), None))
    try:
        assert not state.is_agent_running("member")  # not the bridge: this process's own runs
        assert asked == []
        monkeypatch.setenv("UEFN_DUCKY_MCP_BRIDGE", "1")
        assert state.is_agent_running("member") and state.list_running_agents() == ["member"]
        assert state.linked_children_of("lead") == ["member"] and state.linked_parent_of("member") == "lead"
        assert asked == [""]  # one question, kept for a second
        state.cancel_agent("member")
        assert asked == ["", "member"] and not state.is_agent_running("member")
    finally:
        server.shutdown()
        server.server_close()


def test_a_picture_send_parked_behind_a_stopped_turn_runs_once_idle(monkeypatch) -> None:
    # "Send now" on a queued picture presses Stop; the send reached a turn still shutting
    # down, was parked, and Stop blocked its only trigger: the bubble never ran.
    from backend.agent import a2a_broker as broker

    monkeypatch.setattr(broker, "_retry_queue_later", lambda cid: None)
    monkeypatch.setattr(broker, "_cooldowns", {}, raising=False)
    monkeypatch.setattr(broker, "_stopped", {"pic"}, raising=False)
    running = {"on": True}
    started: list[tuple[str, str, dict]] = []
    patch_turns(monkeypatch, "is_agent_running", lambda cid: running["on"])
    patch_turns(monkeypatch, "wait_for_idle", lambda cid, timeout=None: not running["on"])
    patch_turns(monkeypatch, "_pending_agent_messages", {})
    patch_turns(monkeypatch, "_pending_person_sends", {})
    patch_turns(monkeypatch, "run_message", lambda cid, text, mode, model, **kw: started.append((cid, text, kw)) or "run")
    picture = [{"kind": "image", "path": "attachments/a.png"}]

    assert pending._queue_person_send("pic", "make foam", "agent", {"attachments": picture}) == "queued"
    time.sleep(0.2)
    assert started == []  # still waits behind the turn
    running["on"] = False
    deadline = time.time() + 5
    while not started and time.time() < deadline:
        time.sleep(0.02)
    assert started and started[0][:2] == ("pic", "make foam")
    assert started[0][2]["attachments"] == picture
    assert "pic" not in broker._stopped  # the person's send clears Stop, as a direct send does
