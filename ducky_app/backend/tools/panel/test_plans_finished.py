"""A finished plan refuses new steps, for its lead and for its members."""

import json

from backend.agent.coding_agents import plans
from backend.agent.coding_agents.conftest import _meta_reads_follow_faked_chats  # noqa: F401  (autouse)
from backend.agent.coding_agents.test_plan_assignment import team  # noqa: F401  (fixture)
from backend.tools.panel.plans_finished import FINISHED_PLAN_ERROR, plan_finished


def _finish(root: str) -> None:
    plans.update_node("coord", "a", assignee="group-a", project_root=root)
    for node_id in ("a1", "a2", "b"):
        plans.update_node("coord", node_id, status="completed", project_root=root)
    assert plans.load_plan("coord", root)["status"] == "finished"


def test_an_open_plan_is_not_finished_and_a_ticked_off_one_is(team) -> None:
    assert not plan_finished("coord", team)
    _finish(team)
    assert plan_finished("coord", team)
    assert plan_finished("builder", team)  # a member's add lands in its lead's plan
    assert not plan_finished("outsider", team)  # no plan at all: add_node answers that


def test_ducky_plan_add_node_refuses_a_finished_plan(team, monkeypatch) -> None:
    from backend.tools.panel import ducky_panel as panel
    from frontend.ui_web.agent_turns.testing import patch_turns

    patch_turns(monkeypatch, "get_active_conv_id", lambda: "")
    monkeypatch.setattr(panel, "_project_root", lambda: team)
    monkeypatch.setenv("DUCKY_CONV_ID", "coord")
    added = json.loads(panel.ducky_plan_add_node(content="Hand-off: docs"))
    assert added["ok"]  # an open plan still takes steps
    plans.update_node("coord", added["node"]["id"], status="completed", project_root=team)
    _finish(team)
    refused = json.loads(panel.ducky_plan_add_node(content="Unrelated work"))
    assert refused == {"ok": False, "error": FINISHED_PLAN_ERROR}
    assert all(n["content"] != "Unrelated work" for n in plans._flatten_nodes(plans.load_plan("coord", team)["nodes"]))
    assert plans.load_plan("coord", team)["status"] == "finished"
