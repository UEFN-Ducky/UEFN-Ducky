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
    from frontend.ui_web import agent_modes
    monkeypatch.setattr(agent_modes, "_resolve_push", lambda _: lambda event: None)
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


def test_unknown_assignee_is_rejected_without_changing_plan(team):
    before = plans.load_plan("coord", project_root=team)
    with pytest.raises(ValueError, match="assignee"):
        plans.update_node("coord", "a", assignee="missing", project_root=team)
    assert plans.load_plan("coord", project_root=team) == before


def test_deleted_member_and_deleted_group_do_not_resolve(team, monkeypatch):
    plans.update_node("coord", "a", assignee="group-a", project_root=team)
    original = project_chats.load_conversation
    monkeypatch.setattr(project_chats, "load_conversation", lambda cid, project_root=None:
                        None if cid == "builder" else original(cid, project_root))
    assert plans.assigned_plan_view("builder", team) is None
    monkeypatch.setattr(project_chats, "load_conversation", lambda cid, project_root=None:
                        None if cid == "group-a" else original(cid, project_root))
    assert plans.assigned_plan_view("builder", team) is None


def test_multiple_matching_plans_refuse_instead_of_latest_save(team):
    plans.update_node("coord", "a", assignee="group-a", project_root=team)
    plans.create_plan("outsider", title="Second", nodes=[
        {"id": "other", "content": "Other work", "assignee": "group-a"}
    ], project_root=team)
    assert plans.assigned_plan_view("builder", team) is None
    with pytest.raises(ValueError, match="Multiple plan assignments"):
        plans.assigned_plan_view("builder", team, report_ambiguity=True)


def test_assignment_invalidation_is_scoped_and_contains_no_plan_body(team, monkeypatch):
    from frontend.ui_web import agent_modes

    events = []
    monkeypatch.setattr(agent_modes, "_resolve_push", lambda _: events.append)
    monkeypatch.setattr(project_chats, "list_all_conversation_metadata", lambda root: [
        SimpleNamespace(id=cid) for cid in ("builder", "group-a", "coord", "outsider")
    ])
    plans.update_node("coord", "a", assignee="group-a", project_root=team)
    assert events == [
        {"type": "plan_assignment_changed", "conv_id": "builder"},
        {"type": "plan_assignment_changed", "conv_id": "group-a"},
    ]
    events.clear()
    plans.update_node("coord", "a", assignee="outsider", project_root=team)
    assert {e["conv_id"] for e in events} == {"builder", "group-a", "outsider"}
    assert all(set(e) == {"type", "conv_id"} for e in events)
    events.clear()
    plans.update_node("coord", "a", assignee="", project_root=team)
    assert events == [{"type": "plan_assignment_changed", "conv_id": "outsider"}]


def test_create_plan_rejects_invalid_assignment(team):
    with pytest.raises(ValueError, match="assignee"):
        plans.create_plan("outsider", nodes=[
            {"content": "Work", "assignee": "missing"}
        ], project_root=team)
    assert plans.load_plan("outsider", team) is None
