"""Completion hooks: what runs when a plan step or section completes."""

from __future__ import annotations

import pytest

from backend.agent import verify_evidence
from backend.agent.coding_agents import plan_hooks, plans, tick_gate


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKY_STORE_BACKEND_PLANS", "files")
    root = str(tmp_path)
    plans.create_plan("lead", nodes=[
        {"id": "sec", "content": "Seat work", "children": [
            {"id": "s1", "content": "First"}, {"id": "s2", "content": "Second"}]},
    ], project_root=root)
    plans.update_node("lead", "s1", status="in_progress", project_root=root)
    yield root
    plans.drain_after_save()


def _status(root: str, node_id: str) -> str:
    return plans._walk_find(plans.load_plan("lead", root)["nodes"], node_id)[2]["status"]


def test_a_refusing_gate_keeps_the_step_open(store, monkeypatch):
    seen = []
    message = "Can't tick: 2 files in your lane aren't committed: a.py, b.py."
    monkeypatch.setattr(verify_evidence, "current_conversation", lambda: "member")
    monkeypatch.setattr(tick_gate, "check_tick", lambda cid, node: seen.append((cid, node["id"])) or message)
    with pytest.raises(ValueError, match="aren't committed"):
        plans.update_node("lead", "s1", status="completed", project_root=store)
    assert _status(store, "s1") == "in_progress"
    assert seen == [("member", "s1")]  # the member calling, not the lead whose plan it is


def test_a_passing_gate_completes_the_step(store, monkeypatch):
    monkeypatch.setattr(tick_gate, "check_tick", lambda cid, node: None)
    plans.update_node("lead", "s1", status="completed", project_root=store)
    assert _status(store, "s1") == "completed"


def test_a_broken_gate_never_blocks_the_tick(store, monkeypatch):
    def boom(cid, node):
        raise RuntimeError("git missing")
    monkeypatch.setattr(tick_gate, "check_tick", boom)
    plans.update_node("lead", "s1", status="completed", project_root=store)
    assert _status(store, "s1") == "completed"


@pytest.fixture
def team(tmp_path, monkeypatch):
    """A lead's plan with two seats' sections, fake lanes and a fake hand-over table."""
    from types import SimpleNamespace

    from backend.tools.panel import swarm_build
    from frontend.ui_web.chats import conversations as chat_convs

    monkeypatch.setenv("DUCKY_STORE_BACKEND_PLANS", "files")
    monkeypatch.setattr(chat_convs, "load_conversation_meta",
                        lambda cid, project_root=None: SimpleNamespace(parent_conv_id="grp"))
    monkeypatch.setattr(tick_gate, "check_tick", lambda cid, node: None)
    from backend.agent import a2a_client
    monkeypatch.setattr(a2a_client, "send_notice", lambda **kw: None)
    lanes = {"seat-a": ["x.py", "y.py"], "seat-b": ["z.py"]}
    sets: list[tuple[str, list[str]]] = []
    table: list[dict] = []

    def set_lane(conv, lane):
        sets.append((conv, lane))
        lanes[conv] = lane

    monkeypatch.setattr(plan_hooks, "_lane", lambda conv: lanes.get(conv))
    monkeypatch.setattr(plan_hooks, "_set_lane", set_lane)
    monkeypatch.setattr(swarm_build, "handovers_for", lambda nid: [r for r in table if r["from_section"] == nid])
    seated: list[tuple[str, str]] = []
    posted: list[str] = []
    monkeypatch.setattr(plan_hooks, "post_finish_card", lambda plan, root=None: posted.append(plan["chat_id"]))
    monkeypatch.setattr(swarm_build, "on_step_completed", lambda plan_id, step_id: seated.append((plan_id, step_id)))
    root = str(tmp_path)
    plans.create_plan("lead", nodes=[
        {"id": "A", "content": "Split", "assignee": "seat-a", "children": [
            {"id": "a1", "content": "Split the file"}, {"id": "a2", "content": "Tests"}]},
        {"id": "B", "content": "Composer", "assignee": "seat-b", "children": [{"id": "b1", "content": "Use it"}]},
    ], project_root=root)
    yield SimpleNamespace(root=root, lanes=lanes, sets=sets, table=table, seated=seated, posted=posted)
    plans.drain_after_save()


def _tick(root: str, node_id: str) -> None:
    plans.update_node("lead", node_id, status="completed", project_root=root)
    plans.drain_after_save()
    plans.drain_after_save()  # a lead point added by a hook saves again


def test_a_finished_section_hands_its_files_over_once(team):
    team.table.append({"from_section": "A", "to_section": "B", "paths": ["x.py"]})
    _tick(team.root, "a1")
    assert team.sets == []  # the section isn't done yet
    _tick(team.root, "a2")
    assert team.lanes == {"seat-a": ["y.py"], "seat-b": ["z.py", "x.py"]}
    assert len(team.sets) == 2
    _tick(team.root, "b1")
    assert len(team.sets) == 2  # moved once


def test_a_hand_over_tied_to_a_step_moves_when_the_step_completes(team):
    team.table.append({"from_section": "a1", "to_section": "B", "paths": ["x.py"]})
    _tick(team.root, "a1")
    assert team.lanes["seat-b"] == ["z.py", "x.py"] and team.lanes["seat-a"] == ["y.py"]


def test_a_failed_hand_over_keeps_the_tick_and_adds_a_lead_point(team, monkeypatch):
    def refuse(conv, lane):
        raise ValueError("Lane overlaps another member's lane")
    monkeypatch.setattr(plan_hooks, "_set_lane", refuse)
    team.table.append({"from_section": "a1", "to_section": "B", "paths": ["x.py"]})
    _tick(team.root, "a1")
    doc = plans.load_plan("lead", team.root)
    assert plans._walk_find(doc["nodes"], "a1")[2]["status"] == "completed"
    point = [n for n in doc["nodes"] if n["content"].startswith("Move lane by hand")]
    assert len(point) == 1 and "x.py from seat-a to seat-b" in point[0]["content"]
    assert "overlaps" in point[0]["body_markdown"]


def test_completing_the_after_step_starts_the_waiting_seats_once(team):
    plan_id = plans.load_plan("lead", team.root)["id"]
    _tick(team.root, "a1")
    assert sorted(team.seated) == sorted([(plan_id, "a1"), ("lead", "a1")])  # plan-built and preview-built swarms
    _tick(team.root, "a2")
    assert [s for s in team.seated if s[1] == "a1"] == [(plan_id, "a1"), ("lead", "a1")]


def test_a_failing_swarm_hook_keeps_the_tick_and_adds_a_lead_point(team, monkeypatch):
    from backend.tools.panel import swarm_build

    def boom(plan_id, step_id):
        raise RuntimeError("invite failed")
    monkeypatch.setattr(swarm_build, "on_step_completed", boom)
    _tick(team.root, "a1")
    doc = plans.load_plan("lead", team.root)
    assert plans._walk_find(doc["nodes"], "a1")[2]["status"] == "completed"
    assert [n["content"] for n in doc["nodes"] if "by hand" in n["content"]] == [
        "Start the seats waiting on a1 by hand"]


def test_an_assignment_with_a_full_queue_waits_in_the_queue(monkeypatch):
    # The plan only pushes plan_assignment_changed; the member's turn starts through the keeper,
    # which asks K3's seat queue. A full queue keeps it waiting.
    from frontend.ui_web import seat_queue, team_keeper

    started = []
    monkeypatch.setattr(seat_queue, "_room", lambda running: False)
    try:
        assert team_keeper._enqueue("member-x", lambda: started.append(1)) == "queued"
        assert started == [] and seat_queue.is_busy("member-x")
    finally:
        seat_queue.drop("member-x")


def test_the_last_sub_plan_posts_one_finish_card_to_the_lead(team):
    _tick(team.root, "a1")
    _tick(team.root, "a2")
    assert team.posted == []  # B is still open
    _tick(team.root, "b1")
    assert team.posted == ["lead"]


def test_a_plan_without_sub_plans_posts_no_finish_card(store, monkeypatch):
    posted = []
    monkeypatch.setattr(tick_gate, "check_tick", lambda cid, node: None)
    monkeypatch.setattr(plan_hooks, "post_finish_card", lambda plan, root=None: posted.append(plan))
    for sid in ("s1", "s2"):
        plans.update_node("lead", sid, status="completed", project_root=store)
        plans.drain_after_save()
    assert plans.load_plan("lead", store)["status"] == "finished" and posted == []


def test_the_finish_card_lands_in_the_lead_chat_as_a_tool_result(monkeypatch):
    import json
    from types import SimpleNamespace

    from backend.tools.panel import ducky_finish_team
    from frontend.ui_web.chats import conversations as chat_convs, saves as chat_saves

    card = {"ok": True, "ui": "finish_team", "status": "needs_confirm", "lead": "lead"}
    appended = []
    monkeypatch.setattr(ducky_finish_team, "finish_card", lambda plan, root=None: card)
    monkeypatch.setattr(chat_convs, "load_conversation_meta", lambda cid, project_root=None: SimpleNamespace(id=cid))
    monkeypatch.setattr(chat_saves, "append_message", lambda conv, msg, project_root=None: appended.append((conv.id, msg)))
    plan_hooks.post_finish_card({"id": "p1", "chat_id": "lead"})
    (conv_id, msg), = appended
    block, = msg["blocks"]
    assert conv_id == "lead" and msg["role"] == "assistant"
    assert block["name"] == "ducky_finish_team" and block["status"] == "success"
    assert json.loads(block["result"]["data"]) == card


def test_sections_are_not_gated(monkeypatch):
    monkeypatch.setattr(tick_gate, "check_tick", lambda cid, node: "refused")
    plan_hooks.before_complete({}, {"id": "sec", "children": [{"id": "s1"}]}, "lead")
