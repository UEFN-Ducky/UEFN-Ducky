"""A plan tool's tick nudge names the caller's own step, never another seat's."""

from __future__ import annotations

from backend.agent.coding_agents import plan_ticks, plans

PLAN = {"chat_id": "lead", "status": "open", "nodes": [
    {"id": "c", "content": "Seat C", "assignee": "seat-c", "status": "in_progress", "children": [
        {"id": "c1", "content": "C work", "status": "in_progress"}]},
    {"id": "h", "content": "Seat H", "assignee": "group-h", "status": "pending", "children": [
        {"id": "h1", "content": "H done", "status": "completed"},
        {"id": "h2", "content": "H work", "status": "pending"}]},
]}


def test_a_member_gets_a_nudge_only_for_its_own_step(monkeypatch) -> None:
    monkeypatch.setattr(plans, "_chat_and_group_ids", lambda cid, root=None: [cid, "group-h"] if cid == "seat-h" else [cid])
    out = plan_ticks.attach_next_tick({"ok": True}, PLAN, caller="seat-h")
    assert (out["next_tick"]["id"], out["next_tick"]["n"]) == ("h2", "2.2")
    assert "CHECK OFF NOW: 2.2 `h2`" in out["tick_now"] and "c1" not in out["tick_now"]


def test_the_lead_gets_the_first_open_step() -> None:
    out = plan_ticks.attach_next_tick({"ok": True}, PLAN, caller="lead")
    assert out["next_tick"]["id"] == "c1"


def test_a_caller_with_no_steps_gets_no_nudge(monkeypatch) -> None:
    monkeypatch.setattr(plans, "_chat_and_group_ids", lambda cid, root=None: [cid])
    out = plan_ticks.attach_next_tick({"ok": True}, PLAN, caller="outsider")
    assert "next_tick" not in out and "tick_now" not in out
