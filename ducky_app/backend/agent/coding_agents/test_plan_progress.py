"""Cancelled work stays visible without inflating progress denominators."""
import pytest
from backend.agent.coding_agents import plans
from backend.agent.coding_agents.test_plan_assignment import team


@pytest.mark.parametrize("legacy", [False, True])
def test_progress_excludes_cancelled_steps(legacy):
    nodes = [{"id": str(i), "content": str(i), "status": status} for i, status in
             enumerate(["completed", "cancelled", "pending", "in_progress"])]
    plan = {"todos": nodes} if legacy else {"nodes": [dict(nodes[0], children=nodes[1:])]}
    assert plans.todo_progress(plan) == {
        "total": 3, "completed": 1, "cancelled": 1, "pending": 1, "in_progress": 1,
    }


@pytest.mark.parametrize("plan,cancelled", [(None, 0), ({"nodes": []}, 0),
    ({"nodes": [{"status": "cancelled"}]}, 1)])
def test_no_active_steps_has_zero_total(plan, cancelled):
    assert plans.todo_progress(plan) == {
        "total": 0, "completed": 0, "cancelled": cancelled, "pending": 0, "in_progress": 0,
    }


def test_group_and_member_views_share_completed_denominator(team):
    plans.update_node("coord", "a", assignee="group-a", project_root=team)
    plans.update_node("coord", "a2", status="cancelled", project_root=team)
    plans.update_node("coord", "a1", status="completed", project_root=team)
    for chat in ("group-a", "builder"):
        view = plans.assigned_plan_view(chat, project_root=team)
        assert plans.todo_progress(view) == {
            "total": 2, "completed": 2, "cancelled": 1, "pending": 0, "in_progress": 0,
        }
    progress = plans.todo_progress(plans.load_plan("coord", team))
    assert progress["total"] == 3 and progress["completed"] == 2
