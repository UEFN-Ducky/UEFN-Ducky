"""Roster observations use stored identity and canonical plans, without agent starts."""
import json
import pytest

from frontend.settings import PanelSettings
from frontend.ui_web import project_chats as chats, group_orchestrator as groups
import frontend.ui_web.panel_api  # Initialize the public API before its mixin.
from frontend.ui_web.panel_api_chats import PanelApiChatsMixin
from backend.agent.coding_agents import plans


@pytest.fixture
def roster(tmp_path, monkeypatch):
    root = str(tmp_path / "stored")
    settings = PanelSettings.load()
    group = chats.create_conversation(settings, "", title="Team", project_root=root)
    group.is_group = True
    members = [chats.create_conversation(settings, "", title=name, project_root=root) for name in ("Builder", "Designer")]
    for member in members:
        member.parent_conv_id = group.id
        chats.save_conversation(member, root)
    group.group_members = [{"member_conv_id": m.id, "profile_id": "", "name": m.title} for m in members]
    group.leader_conv_id = members[0].id
    chats.save_conversation(group, root)
    # Keep existing roster synchronization outside this read-payload test.
    monkeypatch.setattr(groups, "_rehome_group_roster", lambda group: False)
    monkeypatch.setattr(groups, "sync_group_members_from_folder", lambda group: None)
    monkeypatch.setattr(chats, "project_root_for_slug", lambda slug: root if slug == chats.project_slug(root) else None)
    api = PanelApiChatsMixin()
    monkeypatch.setattr("frontend.ui_web.agent_modes.get_panel_push", lambda: (lambda event: None))
    monkeypatch.setattr(api, "list_running_agents", lambda: [members[0].id])
    coordinator = chats.create_conversation(settings, "", title="Coordinator", project_root=root)
    plan = plans.create_plan(coordinator.id, title="Master", nodes=[{
        "id": "original", "content": "Original task", "status": "in_progress", "assignee": group.id,
    }], project_root=root)
    return api, group, members, root, plan


def test_stored_project_assignment_and_runtime(roster, tmp_path):
    api, group, members, root, plan = roster
    settings = PanelSettings.load()
    settings.uefn_project_root = str(tmp_path / "unrelated-active")
    settings.save()
    plans.create_plan(members[0].id, title="Wrong project", project_root=settings.uefn_project_root)
    rows = api.group_members(group.id)["members"]
    assert [r["observation"]["runtime"] for r in rows] == ["running", "idle"]
    assert [r["observation"]["role"] for r in rows] == ["leader", "member"]
    for row in rows:
        o = row["observation"]
        assert o["group_id"] == group.id and o["project_slug"] == chats.project_slug(root)
        assert o["assignment"] == {"plan_id": plan["id"], "chat_id": plan["chat_id"], "node_id": "original", "title": "Original task", "status": "in_progress"}
        assert o["observed_at"] > 0
        assert "body_markdown" not in str(o)


def test_agent_read_uses_same_roster_payload(roster, monkeypatch):
    from backend.tools.panel import ducky_panel
    api, group, members, root, plan = roster
    expected = api.group_members(group.id)
    monkeypatch.setattr(api, "group_members", lambda group_id: expected)
    monkeypatch.setattr(ducky_panel, "_panel_api", lambda: api)
    assert json.loads(ducky_panel.ducky_group_members(group.id)) == expected


@pytest.mark.parametrize("problem", ["missing", "ambiguous", "deleted", "unresolved_project", "runtime_error", "bridge", "wrong_parent", "wrong_project"])
def test_unknown_observations(roster, monkeypatch, problem):
    api, group, members, root, plan = roster
    if problem == "missing":
        plans.delete_plan(plan["chat_id"], project_root=root)
    elif problem == "ambiguous":
        other = chats.create_conversation(PanelSettings.load(), "", title="Other", project_root=root)
        plans.create_plan(other.id, nodes=[{"id": "other", "content": "Conflict", "assignee": group.id}], project_root=root)
    elif problem == "deleted":
        monkeypatch.setattr("frontend.ui_web.panel_api.load_conversation", lambda cid: group if cid == group.id else None)
    elif problem == "unresolved_project":
        monkeypatch.setattr(chats, "project_root_for_slug", lambda slug: None)
    elif problem == "wrong_parent":
        for member in members:
            member.parent_conv_id = "another-group"
            chats.save_conversation(member, root)
    elif problem == "wrong_project":
        monkeypatch.setattr(chats, "conversation_project_slug", lambda cid: "group-project" if cid == group.id else "other-project")
    elif problem == "bridge":
        monkeypatch.setattr("frontend.ui_web.agent_modes.get_panel_push", lambda: None)
    else:
        monkeypatch.setattr(api, "list_running_agents", lambda: (_ for _ in ()).throw(RuntimeError("private failure")))
    rows = api.group_members(group.id)["members"]
    for row in rows:
        o = row["observation"]
        if problem in ("runtime_error", "bridge"):
            assert o["runtime"] == "unknown" and o["assignment"]
        else:
            assert o["assignment"] is None
        if problem in ("deleted", "wrong_parent", "wrong_project"):
            assert o["runtime"] == "unknown" and o["role"] == "unknown"
        assert "private failure" not in str(o)
