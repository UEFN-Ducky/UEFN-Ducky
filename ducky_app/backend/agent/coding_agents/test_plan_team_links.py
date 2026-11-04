"""A team's main plan shows who works on each step; a member's own plan links back to it."""

from __future__ import annotations

from types import SimpleNamespace as NS

from backend.agent.coding_agents import plans


def _team(monkeypatch):
    convs = [
        NS(id="lead", is_group=False, title="Video Plugin Lead", leader_conv_id="", parent_conv_id="hub-main"),
        NS(id="hub-main", is_group=True, title="Island Media Kit Delivery", leader_conv_id="lead", parent_conv_id=""),
        NS(id="hub-edit", is_group=True, title="Island Media Kit — Edit & Render", leader_conv_id="lead", parent_conv_id=""),
        NS(id="timeline", is_group=False, title="Timeline Editing", leader_conv_id="", parent_conv_id="hub-edit"),
    ]
    main = {"title": "Island Media Kit", "status": "open", "nodes": [
        {"id": "build", "content": "Build slices", "status": "in_progress", "children": [
            {"id": "edit", "content": "Edit & Render group implements the timeline", "status": "in_progress"},
            {"id": "root", "content": "Root reviews changesets", "status": "pending"},
            {"id": "mine", "content": "Edit & Render polish", "status": "pending", "assignee": "timeline"},
        ]},
    ]}
    own = {"title": "Timeline Editing", "status": "open", "nodes": [{"id": "s1", "content": "Do it", "status": "pending"}]}
    by_id = {c.id: c for c in convs}
    monkeypatch.setattr("frontend.ui_web.chats.groups.island_for_conversation", lambda cid, root=None: (convs, []))
    monkeypatch.setattr("frontend.ui_web.chats.conversations.load_conversation", lambda cid, project_root=None: by_id.get(cid))
    monkeypatch.setattr(plans, "load_plan", lambda cid, root=None: {"lead": main, "timeline": own}.get(cid))
    return main, own


def test_the_main_plan_shows_the_group_a_step_names(monkeypatch):
    main, _own = _team(monkeypatch)
    view = plans.with_team_links(main, "lead")
    edit, root, mine = view["nodes"][0]["children"]
    assert edit["assignee_inferred"] == "hub-edit"
    assert "assignee_inferred" not in root
    assert mine["assignee"] == "timeline" and "assignee_inferred" not in mine  # explicit wins
    assert "assignee_inferred" not in main["nodes"][0]["children"][0]  # display only, never saved
    assert "team_plan" not in view


def test_a_members_own_plan_links_back_to_the_main_plan(monkeypatch):
    _main, own = _team(monkeypatch)
    view = plans.with_team_links(own, "timeline")
    assert view["team_plan"] == {"chat_id": "lead", "title": "Island Media Kit", "node_id": "edit"}
