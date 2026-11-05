"""The lead edits its plan at any time; a member manages every step of its own sub plan."""

from __future__ import annotations

import pytest

from backend.agent.coding_agents import plans, tick_gate
from backend.agent.coding_agents.test_plan_assignment import team  # noqa: F401  (fixture)


@pytest.fixture()
def plan(team, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tick_gate, "check_tick", lambda cid, node: None)
    plans.update_node("coord", "a", assignee="group-a", project_root=team)
    plans.update_node("coord", "b", assignee="outsider", project_root=team)
    return team


def _node(root: str, node_id: str) -> dict | None:
    return plans._walk_find(plans.load_plan("coord", root)["nodes"], node_id)[2]


def test_the_lead_edits_moves_and_deletes_a_completed_step_while_playing(plan) -> None:
    plans.update_node("coord", "a1", status="completed", project_root=plan)
    assert plans.plan_structure_locked(plans.load_plan("coord", plan))  # a flag, never a refusal
    plans.update_node("coord", "a1", content="Core, renamed", body_markdown="new notes", project_root=plan)
    assert _node(plan, "a1")["content"] == "Core, renamed"
    plans.move_node("coord", "a1", parent_id="", index=0, project_root=plan)
    assert plans.load_plan("coord", plan)["nodes"][0]["id"] == "a1"
    plans.delete_node("coord", "a1", project_root=plan)
    assert _node(plan, "a1") is None


def test_the_lead_rewrites_completed_steps_with_update_plan(plan) -> None:
    plans.update_node("coord", "a1", status="completed", project_root=plan)
    done = plans.update_plan("coord", nodes=[{"id": "b", "content": "Only this", "status": "completed"}],
                             project_root=plan)
    assert [n["id"] for n in done["nodes"]] == ["b"]


def test_the_lead_edits_and_reopens_a_finished_plan(plan) -> None:
    for sid in ("a1", "a2", "b"):
        plans.update_node("coord", sid, status="completed", project_root=plan)
    assert plans.load_plan("coord", plan)["status"] == "finished"
    plans.update_plan("coord", title="Repair modes v2", project_root=plan)
    plans.update_node("coord", "b", content="Bridge, done", project_root=plan)
    assert plans.update_plan("coord", status="open", project_root=plan)["status"] == "open"
    assert plans.load_plan("coord", plan)["title"] == "Repair modes v2"


def test_un_x_and_un_complete_go_back_to_pending(plan) -> None:
    plans.update_node("coord", "a1", status="cancelled", project_root=plan)
    plans.update_node("coord", "a2", status="completed", project_root=plan)
    assert _node(plan, "a")["status"] == "completed"  # its steps are done or X'd
    plans.update_node("coord", "a1", status="pending", project_root=plan)
    assert _node(plan, "a1")["status"] == "pending"
    assert _node(plan, "a")["status"] != "completed"  # the section is open again
    plans.update_node("coord", "a2", status="pending", project_root=plan)
    assert _node(plan, "a2")["status"] == "pending"


def test_a_member_manages_its_own_sub_plan(plan) -> None:
    plans.update_node("builder", "a1", status="completed", body_markdown="done", project_root=plan)
    plans.update_node("builder", "a1", content="Core (done)", project_root=plan)  # completed: still editable
    assert _node(plan, "a1")["content"] == "Core (done)"
    plans.update_node("builder", "a2", status="cancelled", body_markdown="Cancelled: not needed", project_root=plan)
    plans.update_node("builder", "a2", status="pending", project_root=plan)
    assert _node(plan, "a2")["status"] == "pending"
    plans.add_node("builder", content="Adapters, part 2", parent_id="a", project_root=plan)
    plans.delete_node("builder", "a1", project_root=plan)
    plans.delete_node("builder", "a2", project_root=plan)
    assert [c["content"] for c in _node(plan, "a")["children"]] == ["Adapters, part 2"]
    plans.update_node("builder", "a", body_markdown="Section notes", project_root=plan)  # notes: fine


def test_a_member_is_refused_another_members_step_and_its_section_title(plan) -> None:
    with pytest.raises(ValueError, match=r"Step 2 belongs to outsider's sub plan: ask your lead"):
        plans.update_node("builder", "b", content="Mine now", project_root=plan)
    with pytest.raises(ValueError, match="belongs to outsider"):
        plans.delete_node("builder", "b", project_root=plan)
    with pytest.raises(ValueError, match="notes and status only"):
        plans.update_node("builder", "a", content="Renamed section", project_root=plan)
    assert _node(plan, "b")["content"] == "B Bridge"


def test_a_verify_leaf_ticks_without_evidence_and_says_so(plan) -> None:
    plans.add_node("coord", content="Verify the bridge builds", parent_id="b", project_root=plan)
    leaf = _node(plan, "b")["children"][0]["id"]
    done = plans.update_node("coord", leaf, status="completed", body_markdown="built", project_root=plan)
    node = plans._walk_find(done["nodes"], leaf)[2]
    assert node["status"] == "completed"
    assert node["body_markdown"] == "built\n(no check tool ran this turn)"
