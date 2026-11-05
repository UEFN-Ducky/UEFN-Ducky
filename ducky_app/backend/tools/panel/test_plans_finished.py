"""A finished plan is told apart from a live one; its lead may still add to it, which reopens it."""

import json

from backend.agent.coding_agents import plans
from backend.agent.coding_agents.conftest import _meta_reads_follow_faked_chats  # noqa: F401  (autouse)
from backend.agent.coding_agents.test_plan_assignment import team  # noqa: F401  (fixture)
from backend.tools.panel.plans_finished import plan_finished


def _finish(root: str) -> None:
    plans.update_node("coord", "a", assignee="group-a", project_root=root)
    for node_id in ("a1", "a2", "b"):
        plans.update_node("coord", node_id, status="completed", project_root=root)
    assert plans.load_plan("coord", root)["status"] == "finished"


def test_an_open_plan_is_not_finished_and_a_ticked_off_one_is(team) -> None:
    plans.update_node("coord", "a", assignee="group-a", project_root=team)
    assert not plan_finished("coord", team)
    assert not plan_finished("builder", team, parent_id="a")  # lands in its lead's live plan
    _finish(team)
    assert plan_finished("coord", team)
    assert not plan_finished("outsider", team)  # no plan at all: add_node answers that
    # A finished team plan is no member's team plan any more: add_node says "plan not found".
    assert not plan_finished("builder", team, parent_id="a")


def test_ducky_plan_add_node_reopens_a_finished_plan_for_its_lead(team, monkeypatch) -> None:
    from backend.tools.panel import ducky_panel as panel
    from frontend.ui_web.agent_turns.testing import patch_turns

    patch_turns(monkeypatch, "get_active_conv_id", lambda: "")
    monkeypatch.setattr(panel, "_project_root", lambda: team)
    monkeypatch.setenv("DUCKY_CONV_ID", "coord")
    added = json.loads(panel.ducky_plan_add_node(content="Hand-off: docs"))
    assert added["ok"]  # an open plan still takes steps
    plans.update_node("coord", added["node"]["id"], status="completed", project_root=team)
    _finish(team)
    added = json.loads(panel.ducky_plan_add_node(content="Fix found later"))
    assert added["ok"]
    assert any(n["content"] == "Fix found later" for n in plans._flatten_nodes(plans.load_plan("coord", team)["nodes"]))
    assert plans.load_plan("coord", team)["status"] == "open"
