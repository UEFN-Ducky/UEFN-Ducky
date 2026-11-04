"""A member finds its lead's plan when the plan is stored under another project root."""
import json

from backend.agent.coding_agents import plans
from backend.agent.coding_agents.conftest import _meta_reads_follow_faked_chats  # noqa: F401  (autouse)
from backend.agent.coding_agents.test_plan_assignment import team  # noqa: F401  (fixture)


def test_member_reads_and_ticks_a_plan_stored_under_another_project(team, tmp_path, monkeypatch):
    # Oct 25 2026: the lead chat was made before the project switched, so its plan stayed
    # under project A while the members' tool servers ran under project B.
    from backend.tools.panel import ducky_panel as panel
    from frontend.ui_web import known_projects
    from frontend.ui_web.agent_turns.testing import patch_turns
    project_a = team
    project_b = str(tmp_path / "project-b")
    plans.update_node("coord", "a", assignee="group-a", project_root=project_a)
    patch_turns(monkeypatch, "get_active_conv_id", lambda: "")
    monkeypatch.setenv("DUCKY_CONV_ID", "builder")
    monkeypatch.setattr(panel, "_project_root", lambda: project_b)
    monkeypatch.setattr(known_projects, "known_project_roots", lambda **_k: [project_b, project_a])
    assert plans.load_plan_view("builder", project_root=project_b) is None

    view = json.loads(panel.ducky_get_plan())
    assert view["ok"] and view["plan"]["assigned_from"]["node_id"] == "a"

    tick = json.loads(panel.ducky_plan_update_node(node_id="a2", status="in_progress"))
    assert tick["ok"] and tick["node"]["status"] == "in_progress"
    assert plans.load_plan("coord", project_a)["nodes"][0]["children"][1]["status"] == "in_progress"

    # Nothing anywhere: still the plain "not found" answer.
    monkeypatch.setattr(known_projects, "known_project_roots", lambda **_k: [project_b])
    assert not json.loads(panel.ducky_plan_update_node(node_id="a2", status="completed"))["ok"]
