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

@pytest.mark.parametrize('backend', ['files', 'db'])
def test_deleted_plan_owner_cannot_hide_valid_assignment(team, monkeypatch, backend):
    monkeypatch.setenv('DUCKY_STORE_BACKEND_PLANS', backend)
    plans.update_node('coord', 'a', assignee='group-a', project_root=team)
    plans.create_plan('outsider', title='Live plan', nodes=[
        {'id': 'live-node', 'content': 'Live work', 'assignee': 'group-a'}
    ], project_root=team)
    original = project_chats.load_conversation
    monkeypatch.setattr(project_chats, 'load_conversation', lambda cid, project_root=None:
                        None if cid == 'coord' else original(cid, project_root))
    view = plans.assigned_plan_view('builder', team, report_ambiguity=True)
    assert view['assigned_from']['chat_id'] == 'outsider'
    assert view['assigned_from']['node_id'] == 'live-node'


@pytest.mark.parametrize('backend', ['files', 'db'])
def test_assignment_reload_keeps_canonical_identity_and_project_scope(team, monkeypatch, backend, tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path
    from backend.store import db
    monkeypatch.setenv('DUCKY_STORE_BACKEND_PLANS', backend)
    master = plans.update_node('coord', 'a', assignee='group-a', project_root=team)
    plans.update_node('coord', 'a2', assignee='builder', project_root=team)
    db.close_thread_connections()
    view = plans.assigned_plan_view('builder', team)
    assert view['id'] == master['id']
    assert view['assigned_from']['node_id'] == 'a2'
    assert view['nodes'][0]['id'] == 'a2'
    monkeypatch.setenv('PYTHONPATH', str(Path(plans.__file__).resolve().parents[3]))
    restarted = subprocess.run([sys.executable, '-B', '-c', '''
import json, sys
from types import SimpleNamespace
from backend.agent.coding_agents import plans
from frontend.ui_web import project_chats
parents = {'builder': 'group-a', 'group-a': 'team', 'coord': 'team', 'team': ''}
project_chats.load_conversation = lambda cid, project_root=None: (
    SimpleNamespace(parent_conv_id=parents[cid]) if cid in parents else None)
print(json.dumps(plans.assigned_plan_view('builder', sys.argv[1])))
''', team], capture_output=True, text=True, check=True, timeout=15)
    assert json.loads(restarted.stdout)['assigned_from'] == view['assigned_from']
    assert json.loads(restarted.stdout)['id'] == master['id']
    assert plans.load_plan('builder', team) is None
    assert plans.assigned_plan_view('builder', str(tmp_path / 'other-project')) is None
    plans.update_node('coord', 'a2', assignee='', project_root=team)
    db.close_thread_connections()
    assert plans.assigned_plan_view('builder', team)['assigned_from']['node_id'] == 'a'
    plans.delete_plan('coord', team)
    assert plans.assigned_plan_view('builder', team) is None


def test_existing_personal_plan_keeps_precedence(team):
    from frontend.ui_web import panel_api  # initialize the public API before its mixin
    from frontend.ui_web.panel_api_settings import PanelApiSettingsMixin
    plans.update_node('coord', 'a', assignee='group-a', project_root=team)
    personal = plans.create_plan('builder', title='Personal', nodes=[
        {'id': 'own', 'content': 'Own work'}
    ], project_root=team)
    view = PanelApiSettingsMixin().get_plan('builder', project_root=team)['plan']
    assert view['id'] == personal['id']
    assert 'assigned_from' not in view

@pytest.mark.parametrize('backend', ['files', 'db'])
def test_deleted_assigned_node_falls_back_without_changing_master(team, monkeypatch, backend):
    monkeypatch.setenv('DUCKY_STORE_BACKEND_PLANS', backend)
    master = plans.update_node('coord', 'a', assignee='group-a', project_root=team)
    plans.update_node('coord', 'a2', assignee='builder', project_root=team)
    plans.update_plan('coord', status='paused', project_root=team)
    plans.delete_node('coord', 'a2', project_root=team)
    view = plans.assigned_plan_view('builder', team)
    assert view['id'] == master['id']
    assert view['assigned_from']['node_id'] == 'a'
    assert [n['id'] for n in view['nodes'][0]['children']] == ['a1']

@pytest.mark.parametrize('backend', ['files', 'db'])
@pytest.mark.parametrize('binding', ['valid', 'missing', 'deleted_owner', 'ambiguous', 'other_project', 'archived_master'])
def test_archived_personal_reference_never_masks_canonical_assignment(team, monkeypatch, backend, binding, tmp_path):
    from frontend.ui_web import panel_api
    from frontend.ui_web.panel_api_settings import PanelApiSettingsMixin
    monkeypatch.setenv('DUCKY_STORE_BACKEND_PLANS', backend)
    archived = plans.save_plan({
        'id': 'original-reference-id', 'kind': 'project', 'chat_id': 'builder',
        'title': 'Archived assignment reference - use original master subplan',
        'overview': 'Old prose refers to some-other-master',
        'body_markdown': 'Obsolete instructions are history only.',
        'nodes': [], 'todos': [], 'status': 'archived', 'created_at': 1,
    }, team)
    master = plans.load_plan('coord', team)
    if binding != 'missing':
        master = plans.update_node('coord', 'a', assignee='group-a', project_root=team)
    if binding == 'deleted_owner':
        original = project_chats.load_conversation
        monkeypatch.setattr(project_chats, 'load_conversation', lambda cid, project_root=None:
                            None if cid == 'coord' else original(cid, project_root))
    if binding == 'ambiguous':
        plans.create_plan('outsider', nodes=[{'id': 'other', 'content': 'Other', 'assignee': 'group-a'}], project_root=team)
    if binding == 'archived_master':
        plans.update_plan('coord', status='archived', project_root=team)
    root = str(tmp_path / 'separate-project') if binding == 'other_project' else team
    if binding == 'other_project':
        plans.save_plan(archived, root)
    result = PanelApiSettingsMixin().get_plan('builder', project_root=root)
    if binding == 'valid':
        assert result['plan']['id'] == master['id']
        assert result['plan']['assigned_from']['node_id'] == 'a'
    else:
        assert result['plan'] is None
        if binding == 'ambiguous':
            assert result['ok'] is False
            assert 'Multiple plan assignments' in result['error']
    assert plans.load_plan('builder', team) == archived
    assert next(p for p in plans.list_plans(team) if p['chat_id'] == 'builder')['status'] == 'archived'


@pytest.mark.parametrize('backend', ['files', 'db'])
def test_archived_master_does_not_conflict_with_live_assignment(team, monkeypatch, backend):
    monkeypatch.setenv('DUCKY_STORE_BACKEND_PLANS', backend)
    plans.update_node('coord', 'a', assignee='group-a', project_root=team)
    plans.update_plan('coord', status='archived', project_root=team)
    live = plans.create_plan('outsider', nodes=[{'id': 'live', 'content': 'Live', 'assignee': 'group-a'}], project_root=team)
    assert plans.assigned_plan_view('builder', team, report_ambiguity=True)['id'] == live['id']
