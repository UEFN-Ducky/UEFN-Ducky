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
    plans.drain_after_save()
    yield root
    plans.drain_after_save()  # a save's side work never runs into the next test


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
        "node_ids": ["a"],
        "plan_title": "Repair modes",
        "assignee": "group-a",
    }
    assert view["title"] == "Your sub plan in “Repair modes”"  # the team plan's name, never a step's
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


def test_a_sub_plan_without_steps_cannot_be_assigned(team) -> None:
    plans.add_node("coord", content="UI", kind="subplan", project_root=team)
    ui = plans.load_plan("coord", team)["nodes"][-1]["id"]
    with pytest.raises(ValueError, match="Add its steps first, then assign it"):
        plans.update_node("coord", ui, assignee="builder", project_root=team)
    with pytest.raises(ValueError, match="Add its steps first"):
        plans.add_node("coord", content="Art", kind="subplan", assignee="builder", project_root=team)
    for step in ("HUD", "Menu"):
        plans.add_node("coord", content=step, parent_id=ui, project_root=team)
    plan = plans.update_node("coord", ui, assignee="builder", project_root=team)
    assert plans._walk_find(plan["nodes"], ui)[2]["assignee"] == "builder"
    plan = plans.update_node("coord", "b", assignee="builder", project_root=team)  # a leaf step
    assert plans._walk_find(plan["nodes"], "b")[2]["assignee"] == "builder"


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
    with pytest.raises(ValueError, match="open steps in 2 plans"):
        plans.assigned_plan_view("builder", team, report_ambiguity=True)


def test_assignment_invalidation_is_scoped_and_contains_no_plan_body(team, monkeypatch):
    from frontend.ui_web import agent_modes

    events = []
    monkeypatch.setattr(agent_modes, "_resolve_push", lambda _: events.append)
    parents = {"builder": "group-a", "group-a": "team", "coord": "team", "outsider": ""}
    monkeypatch.setattr(project_chats, "list_all_conversation_metadata", lambda root: [
        SimpleNamespace(id=cid, parent_conv_id=parent) for cid, parent in parents.items()
    ])
    plans.update_node("coord", "a", assignee="group-a", project_root=team)
    plans.drain_after_save()
    assert events == [
        {"type": "plan_assignment_changed", "conv_id": "builder"},
        {"type": "plan_assignment_changed", "conv_id": "group-a"},
    ]
    events.clear()
    plans.update_node("coord", "a", assignee="outsider", project_root=team)
    plans.drain_after_save()
    assert {e["conv_id"] for e in events} == {"builder", "group-a", "outsider"}
    assert all(set(e) == {"type", "conv_id"} for e in events)
    events.clear()
    plans.update_node("coord", "a", assignee="", project_root=team)
    plans.drain_after_save()
    assert events == [{"type": "plan_assignment_changed", "conv_id": "outsider"}]


def test_a_tick_refreshes_every_owners_plan_bar(team, monkeypatch):
    # Oct 24 2026: only the lead got plan_updated, so a member's "Your sub plan" bar sat at 0/9.
    from frontend.ui_web import agent_modes

    plans.update_node("coord", "a", assignee="group-a", project_root=team)
    plans.update_node("coord", "b", assignee="outsider", project_root=team)
    plans.drain_after_save()
    events = []
    monkeypatch.setattr(agent_modes, "_resolve_push", lambda _: events.append)
    monkeypatch.setattr(project_chats, "list_all_conversation_metadata", lambda root: [
        SimpleNamespace(id="builder", parent_conv_id=""),  # seated with as_leader: filed elsewhere
        SimpleNamespace(id="group-a", parent_conv_id="team", is_group=True, leader_conv_id="builder",
                        group_members=[{"member_conv_id": "builder"}]),
        SimpleNamespace(id="coord", parent_conv_id="team"), SimpleNamespace(id="outsider", parent_conv_id="")])
    plan = plans.update_node("coord", "a2", status="completed", project_root=team)
    plans.push_plan_updated(plan)  # the lead's own event, sent by the plan tool
    plans.drain_after_save()
    updated = {e["conv_id"] for e in events if e["type"] == "plan_updated"}
    assert updated == {"coord", "builder", "group-a", "outsider"}
    assert len([e for e in events if e["conv_id"] == "builder"]) == 1  # deduped
    assert all("plan" not in e for e in events if e["conv_id"] != "coord")  # no copy of the lead's plan
    assert {e.get("plan_id") for e in events if e["conv_id"] != "coord"} == {plan["id"]}


def test_a_tick_on_a_big_team_plan_returns_fast(team, monkeypatch):
    # Oct 24 2026: a tick took 4-15 s. Each save walked every chat of the project with
    # load_conversation (whole histories) under the plan lock, to tell the members.
    import time as clock
    from frontend.ui_web import agent_modes

    seats = [f"m{i}" for i in range(6)]
    convs = {cid: SimpleNamespace(parent_conv_id="team") for cid in [*seats, "coord", "team"]}
    monkeypatch.setattr(project_chats, "load_conversation", lambda cid, project_root=None: convs.get(cid))
    nodes = [{"id": f"s{i}", "content": f"Part {i}", "assignee": seat, "children": [
        {"id": f"s{i}-{j}", "content": f"Step {j}"} for j in range(8)]} for i, seat in enumerate(seats)]
    plans.create_plan("lead", title="Big", nodes=nodes, project_root=team, team_check=False)
    plans.drain_after_save()
    events = []
    monkeypatch.setattr(agent_modes, "_resolve_push", lambda _: events.append)

    def slow_listing(root):
        clock.sleep(1.0)  # a big project's chat list
        return [SimpleNamespace(id=cid, parent_conv_id="team") for cid in convs]

    monkeypatch.setattr(project_chats, "list_all_conversation_metadata", slow_listing)
    started = clock.perf_counter()
    for j in range(4):
        plans.update_node("lead", f"s0-{j}", status="completed", project_root=team)
    assert (clock.perf_counter() - started) / 4 < 0.3
    plans.drain_after_save()
    assert {e["conv_id"] for e in events} == set(seats)  # the side work still happened


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
project_chats.load_conversation = project_chats.load_conversation_meta = lambda cid, project_root=None: (
    SimpleNamespace(parent_conv_id=parents[cid]) if cid in parents else None)
print(json.dumps(plans.assigned_plan_view('builder', sys.argv[1])))
''', team], capture_output=True, text=True, check=True, timeout=15)
    assert json.loads(restarted.stdout)['assigned_from'] == view['assigned_from']
    assert json.loads(restarted.stdout)['id'] == master['id']
    assert plans.load_plan('builder', team) is None
    elsewhere = plans.assigned_plan_view('builder', str(tmp_path / 'other-project'))
    if backend == 'db':  # a plan follows its ducky: naming another project still finds it
        assert elsewhere['id'] == master['id']
    else:
        assert elsewhere is None
    plans.update_node('coord', 'a2', assignee='', project_root=team)
    db.close_thread_connections()
    assert plans.assigned_plan_view('builder', team)['assigned_from']['node_id'] == 'a'
    plans.delete_plan('coord', team)
    assert plans.assigned_plan_view('builder', team) is None


def test_open_team_steps_outrank_a_personal_plan(team):
    # Oct 12 2026: a member's private plan hid the step its lead assigned it.
    from frontend.ui_web.panel_api_settings import PanelApiSettingsMixin
    plans.update_node('coord', 'a', assignee='group-a', project_root=team)
    personal = plans.create_plan('builder', title='Personal', nodes=[
        {'id': 'own', 'content': 'Own work'}
    ], project_root=team, team_check=False)
    view = PanelApiSettingsMixin().get_plan('builder', project_root=team)['plan']
    assert view['assigned_from']['chat_id'] == 'coord'
    # With no open team step, the personal plan shows.
    plans.update_node('coord', 'a1', status='completed', project_root=team)
    plans.update_node('coord', 'a2', status='completed', project_root=team)
    view = PanelApiSettingsMixin().get_plan('builder', project_root=team)['plan']
    assert view['id'] == personal['id']
    assert 'assigned_from' not in view


def test_a_member_cannot_make_a_second_plan(team):
    plans.update_node('coord', 'a', assignee='group-a', project_root=team)
    with pytest.raises(ValueError, match="ONE plan") as err:
        plans.create_plan('builder', title='Mine', nodes=[{'id': 'x', 'content': 'X'}], project_root=team)
    assert 'chat_id="coord"' in str(err.value)
    assert plans.load_plan('builder', team) is None
    # Outside the team nothing changes.
    plans.create_plan('outsider', title='Solo', nodes=[{'id': 'x', 'content': 'X'}], project_root=team)


def test_a_group_leads_plan_is_its_members_plan(team, monkeypatch):
    convs = {
        "builder": SimpleNamespace(parent_conv_id="crew"),
        "crew": SimpleNamespace(parent_conv_id="", leader_conv_id="coord"),
        "coord": SimpleNamespace(parent_conv_id="crew"),
    }
    monkeypatch.setattr(project_chats, "load_conversation", lambda cid, project_root=None: convs.get(cid))
    # No step assigned yet: the lead's plan is still the team's one plan.
    assert plans.team_plan_for('builder', team)['chat_id'] == 'coord'
    assert plans.team_plan_for('coord', team) is None
    with pytest.raises(ValueError, match="ONE plan"):
        plans.create_plan('builder', title='Mine', project_root=team)


def test_a_member_makes_no_plan_while_its_lead_has_none(team, monkeypatch):
    # Oct 15 2026: a lead handed out work by message before writing its plan, a member wrote a
    # plan of its own, and the keeper woke that member as the team's lead.
    convs = {
        "builder": SimpleNamespace(parent_conv_id="crew"),
        "crew": SimpleNamespace(parent_conv_id="", leader_conv_id="lead"),
        "lead": SimpleNamespace(parent_conv_id=""),
        "helper": SimpleNamespace(parent_conv_id="open-crew"),
        "open-crew": SimpleNamespace(parent_conv_id=""),  # the person leads this group
    }
    monkeypatch.setattr(project_chats, "load_conversation", lambda cid, project_root=None: convs.get(cid))
    with pytest.raises(ValueError, match="has no plan yet") as err:
        plans.create_plan("builder", title="Mine", nodes=[{"id": "x", "content": "X"}], project_root=team)
    assert "chat lead" in str(err.value) and "do the task you were given" in str(err.value)
    assert plans.load_plan("builder", team) is None
    # The person may still give the member a plan, and a group the person leads has no lead plan.
    plans.create_plan("helper", title="Solo", project_root=team)
    plans.create_plan("builder", title="Asked for", project_root=team, team_check=False)
    plans.delete_plan("builder", team)
    # Once the lead writes the team plan, that plan is the member's, as before.
    plans.create_plan("lead", title="Team", nodes=[{"id": "s", "content": "S", "assignee": "builder"}],
                      project_root=team)
    with pytest.raises(ValueError, match="on its team's plan"):
        plans.create_plan("builder", title="Mine", project_root=team)


def test_member_step_edits_land_in_the_team_plan(team):
    # Oct 12 2026: ticks without the lead's chat id said "plan not found — call
    # ducky_create_plan first", and members made private plans.
    plans.update_node('coord', 'b', assignee='builder', project_root=team)
    plan = plans.update_node('builder', 'b', status='in_progress', project_root=team)
    assert plan['chat_id'] == 'coord'
    plans.add_node('builder', content='Part one', parent_id='b', project_root=team)  # while playing
    plan = plans.load_plan('coord', team)
    assert [c['content'] for c in plans._walk_find(plan['nodes'], 'b')[2]['children']] == ['Part one']
    assert plans.load_plan('builder', team) is None
    with pytest.raises(ValueError, match="team's plan"):
        plans.update_plan('builder', status='paused', project_root=team)


def test_a_step_waits_for_its_after_steps(team):
    plans.update_node('coord', 'b', assignee='builder', after=['a'], project_root=team)
    with pytest.raises(ValueError, match="waits for"):
        plans.update_node('builder', 'b', status='in_progress', project_root=team)
    view = plans.assigned_plan_view('builder', team)
    assert [w['id'] for w in view['nodes'][0]['waiting_on']] == ['a']
    assert "waits for `a`" in plans.format_plan_prompt_block(view)
    with pytest.raises(ValueError, match="no step nope"):
        plans.update_node('coord', 'b', after=['nope'], project_root=team)
    for step in ('a1', 'a2'):
        plans.update_node('coord', step, status='completed', project_root=team)
    plans.update_node('builder', 'b', status='in_progress', project_root=team)
    plans.update_node('coord', 'b', after=[], project_root=team)
    assert 'after' not in plans._walk_find(plans.load_plan('coord', team)['nodes'], 'b')[2]


def test_a_lead_adds_an_owned_step_while_the_team_plays(team):
    plan = plans.add_node('coord', content='Fix the blocker', assignee='builder', after=['a1'],
                          project_root=team)
    added = plan['nodes'][-1]
    assert added['assignee'] == 'builder' and added['after'] == ['a1']
    plans.update_node('coord', 'a1', status='completed', project_root=team)
    with pytest.raises(ValueError, match="frozen"):
        plans.add_node('coord', content='Under a finished step', parent_id='a1', project_root=team)

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
    if binding == 'other_project' and backend == 'files':  # the database keeps one per ducky
        plans.save_plan(archived, root)
    result = PanelApiSettingsMixin().get_plan('builder', project_root=root)
    # The database keeps plans with their duckies: another project finds the same one.
    if binding == 'valid' or (binding == 'other_project' and plans._use_db()):
        assert result['plan']['id'] == master['id']
        assert result['plan']['assigned_from']['node_id'] == 'a'
    else:
        assert result['plan'] is None
        if binding == 'ambiguous':
            assert result['ok'] is False
            assert 'open steps in 2 plans' in result['error']
    assert plans.load_plan('builder', team) == archived
    assert next(p for p in plans.list_plans(team) if p['chat_id'] == 'builder')['status'] == 'archived'


@pytest.mark.parametrize('backend', ['files', 'db'])
def test_archived_master_does_not_conflict_with_live_assignment(team, monkeypatch, backend):
    monkeypatch.setenv('DUCKY_STORE_BACKEND_PLANS', backend)
    plans.update_node('coord', 'a', assignee='group-a', project_root=team)
    plans.update_plan('coord', status='archived', project_root=team)
    live = plans.create_plan('outsider', nodes=[{'id': 'live', 'content': 'Live', 'assignee': 'group-a'}], project_root=team)
    assert plans.assigned_plan_view('builder', team, report_ambiguity=True)['id'] == live['id']


def test_a_finished_part_never_hides_the_members_new_work(team):
    # Oct 10 2026: a writer's section in a finished plan made its next assignment
    # "ambiguous", so its chat showed no plan while it had work to do.
    plans.update_node("coord", "a", assignee="group-a", project_root=team)
    plans.update_node("coord", "a1", status="completed", project_root=team)
    plans.update_node("coord", "a2", status="completed", project_root=team)
    plans.create_plan("outsider", title="Next round", nodes=[
        {"id": "next", "content": "Next work", "assignee": "group-a"}
    ], project_root=team)
    view = plans.assigned_plan_view("builder", team, report_ambiguity=True)
    assert view["assigned_from"]["chat_id"] == "outsider" and view["title"] == "Your step in “Next round”"
    # Its new part done too: nothing to act on, so the latest part is shown, not an error.
    plans.update_node("outsider", "next", status="completed", project_root=team)
    view = plans.assigned_plan_view("builder", team, report_ambiguity=True)
    assert view["assigned_from"]["chat_id"] == "outsider"


def test_a_builder_with_several_steps_in_one_plan_sees_them_all(team) -> None:
    # Oct 11 2026: every builder of a new team owned two or three steps and got "Multiple plan
    # assignments ... the coordinator must clear the conflicting assignments" on its first move.
    plans.update_node("coord", "a2", assignee="builder", project_root=team)
    plans.update_node("coord", "b", assignee="builder", project_root=team)
    view = plans.load_plan_view("builder", project_root=team)
    assert [n["id"] for n in view["nodes"]] == ["a2", "b"]
    assert view["assigned_from"]["node_ids"] == ["a2", "b"]
    assert view["title"] == "Your 2 steps in “Repair modes”"


def test_a_real_conflict_goes_to_the_teams_own_lead_or_says_how_to_fix_it(team, monkeypatch) -> None:
    plans.update_node("coord", "a", assignee="builder", project_root=team)
    plans.create_plan("outsider", title="Side quest", nodes=[{"id": "x", "content": "X", "assignee": "builder"}],
                      project_root=team)
    with pytest.raises(ValueError) as err:
        plans.load_plan_view("builder", project_root=team)
    text = str(err.value)
    assert "open steps in 2 plans" in text and "Repair modes" in text and "Side quest" in text
    assert "`a`" in text and "`x`" in text and "ducky_plan_update_node" in text and "paused" in text
    # The builder's group is led by coord: coord's plan is its work.
    convs = {"builder": SimpleNamespace(parent_conv_id="group-a"),
             "group-a": SimpleNamespace(parent_conv_id="team", leader_conv_id="coord"),
             "team": SimpleNamespace(parent_conv_id=""), "coord": SimpleNamespace(parent_conv_id="team"),
             "outsider": SimpleNamespace(parent_conv_id="")}
    monkeypatch.setattr(project_chats, "load_conversation", lambda cid, project_root=None: convs.get(cid))
    assert plans.load_plan_view("builder", project_root=team)["chat_id"] == "coord"


def test_plan_review_asks_for_one_sub_plan_per_agent():
    loose = {"nodes": [
        {"id": "x", "content": "Fix X", "assignee": "builder", "body_markdown": "Do X."},
        {"id": "y", "content": "Fix Y", "assignee": "builder", "body_markdown": "Do Y."},
        {"id": "z", "content": "Fix Z", "body_markdown": "Do Z."},
    ]}
    issues = [i["issue"] for i in plans.plan_review(loose)]
    assert any("2 top-level steps are assigned to builder" in i for i in issues)
    assert any("No owner" in i for i in issues)
    grouped = {"nodes": [{"id": "sec", "content": "Builder's part", "assignee": "builder",
                          "body_markdown": "Fixes X and Y.", "children": [
                              {"id": "x", "content": "Fix X", "body_markdown": "Do X."},
                              {"id": "y", "content": "Fix Y", "body_markdown": "Do Y."}]}]}
    # Thin bodies and missing Files are test_plan_review's checks; here only grouping and owners count.
    assert not [i for i in plans.plan_review(grouped) if "top-level" in i["issue"] or "No owner" in i["issue"]]
