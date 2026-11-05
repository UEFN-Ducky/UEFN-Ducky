"""A lead closing its plan X's the leftovers, and X'd steps leave the progress total."""

from __future__ import annotations

import pytest

from backend.agent.coding_agents import plans, tick_gate


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKY_STORE_BACKEND_PLANS", "files")
    monkeypatch.setattr(tick_gate, "check_tick", lambda cid, node: None)
    monkeypatch.setattr(plans, "_push_assignment_invalidated", lambda *args: None)
    yield str(tmp_path)
    plans.drain_after_save()


def _find(plan: dict, node_id: str) -> dict:
    return plans._walk_find(plan["nodes"], node_id)[2]


def test_closing_a_plan_xs_its_open_steps_with_the_reason(root) -> None:
    plans.create_plan("lead", nodes=[
        {"id": "s", "content": "Seat", "children": [{"id": "s1", "content": "Build"}, {"id": "s2", "content": "Docs"}]},
        {"id": "t", "content": "Live test", "body_markdown": "Run it on the shipped build."},
    ], project_root=root)
    plans.update_node("lead", "s1", status="completed", project_root=root)
    plans.update_node("lead", "t", status="in_progress", project_root=root)
    closed = plans.update_plan("lead", status="finished", reason="released as 1.2.469", project_root=root)
    assert closed["status"] == "finished"
    for sid in ("s2", "t"):
        node = _find(closed, sid)
        assert node["status"] == "cancelled"
        assert node["body_markdown"].endswith("Cancelled: plan closed — released as 1.2.469")
    assert _find(closed, "t")["body_markdown"].startswith("Run it on the shipped build.")
    assert _find(closed, "s")["status"] == "completed"  # its last open step X'd: it completes itself
    assert plans.todo_progress(closed)["completed"] == plans.todo_progress(closed)["total"]
    assert plans.load_plan("lead", root)["status"] == "finished"


def test_a_subplan_with_one_completed_and_one_xd_step_completes_itself(root) -> None:
    plans.create_plan("lead", nodes=[
        {"id": "s", "content": "Seat", "children": [{"id": "s1", "content": "Build"}, {"id": "s2", "content": "Docs"}]},
        {"id": "o", "content": "Other"},
    ], project_root=root)
    plans.update_node("lead", "s1", status="completed", project_root=root)
    plan = plans.update_node("lead", "s2", status="cancelled", body_markdown="Cancelled: not needed", project_root=root)
    assert _find(plan, "s")["status"] == "completed"
    progress = plans.todo_progress(plan)
    assert (progress["completed"], progress["total"], progress["cancelled"]) == (2, 3, 1)  # s, s1 of s, s1, o
