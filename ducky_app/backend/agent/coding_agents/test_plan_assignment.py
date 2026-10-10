"""A team member's chat shows its group's part of the coordinator's plan."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.agent.coding_agents import plans
from frontend.ui_web import project_chats

NODES = [
    {
        "id": "a",
        "content": "A Modes",
        "children": [
            {"id": "a1", "content": "Core"},
            {"id": "a2", "content": "Adapters"},
        ],
    },
    {"id": "b", "content": "B Bridge"},
]


@pytest.fixture()
def team(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKY_STORE_BACKEND_PLANS", "files")
    convs = {
        "builder": SimpleNamespace(parent_conv_id="group-a"),
        "group-a": SimpleNamespace(parent_conv_id="team"),
        "team": SimpleNamespace(parent_conv_id=""),
        "coord": SimpleNamespace(parent_conv_id="team"),
        "outsider": SimpleNamespace(parent_conv_id=""),
    }
    monkeypatch.setattr(project_chats, "load_conversation", lambda cid, project_root=None: convs.get(cid))
    root = str(tmp_path)
    plans.create_plan("coord", title="Repair modes", nodes=NODES, project_root=root)
    plans.update_node("coord", "a1", status="in_progress", project_root=root)  # the plan is playing
    return root


def test_an_owner_can_be_set_while_the_plan_plays(team) -> None:
    plan = plans.update_node("coord", "a", assignee="group-a", project_root=team)
    assert plans._walk_find(plan["nodes"], "a")[2]["assignee"] == "group-a"
    assert plans.load_plan("coord", project_root=team)["nodes"][0]["assignee"] == "group-a"
    plan = plans.update_node("coord", "a", assignee="", project_root=team)
    assert "assignee" not in plans._walk_find(plan["nodes"], "a")[2]


def test_a_member_sees_its_groups_part(team) -> None:
    plans.update_node("coord", "a", assignee="group-a", project_root=team)
    view = plans.assigned_plan_view("builder", project_root=team)
    assert view["assigned_from"] == {
        "chat_id": "coord",
        "node_id": "a",
        "plan_title": "Repair modes",
        "assignee": "group-a",
    }
    assert view["title"] == "A Modes"
    assert [n["id"] for n in view["nodes"]] == ["a"]
    assert [t["id"] for t in view["todos"]] == ["a", "a1", "a2"]
    # The group chat itself sees it too; someone outside the team does not.
    assert plans.assigned_plan_view("group-a", project_root=team)["assigned_from"]["node_id"] == "a"
    assert plans.assigned_plan_view("outsider", project_root=team) is None


def test_the_most_specific_owner_wins(team) -> None:
    plans.update_node("coord", "a", assignee="group-a", project_root=team)
    plans.update_node("coord", "a2", assignee="builder", project_root=team)
    assert plans.assigned_plan_view("builder", project_root=team)["assigned_from"]["node_id"] == "a2"
    plans.update_node("coord", "a2", assignee="", project_root=team)
    assert plans.assigned_plan_view("builder", project_root=team)["assigned_from"]["node_id"] == "a"


def test_the_coordinators_own_plan_is_never_replaced(team) -> None:
    plans.update_node("coord", "b", assignee="team", project_root=team)
    # coord sits in "team", but its own plan is not offered back to it as a view.
    assert plans.assigned_plan_view("coord", project_root=team) is None
