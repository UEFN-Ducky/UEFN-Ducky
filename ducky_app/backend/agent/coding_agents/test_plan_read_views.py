"""Canonical assignment reads through MCP and runtime prompt boundaries."""
import json
from types import SimpleNamespace

import pytest
from backend.agent.coding_agents import plans
from backend.agent.coding_agents.test_plan_assignment import team  # noqa: F401  (fixture)
from frontend.ui_web.chats import conversations as chat_convs


@pytest.mark.parametrize('backend', ['files', 'db'])
@pytest.mark.parametrize('state', ['assigned', 'archived', 'own', 'missing', 'stale', 'ambiguous', 'other_project'])
def test_mcp_assignment_read_preserves_raw_ownership(team, monkeypatch, backend, state, tmp_path):
    from backend.tools.panel import ducky_panel as panel
    from frontend.ui_web.agent_turns.testing import patch_turns
    monkeypatch.setenv('DUCKY_STORE_BACKEND_PLANS', backend)
    patch_turns(monkeypatch, 'get_active_conv_id', lambda: '')
    monkeypatch.setenv('DUCKY_CONV_ID', 'builder')
    root = team
    if state != 'missing':
        plans.update_node('coord', 'a', assignee='group-a', project_root=team)
    if state == 'archived':
        plans.save_plan({'id': 'history', 'chat_id': 'builder', 'status': 'archived', 'nodes': []}, team)
    if state == 'own':
        plans.create_plan('builder', nodes=[{'id': 'personal', 'content': 'Personal'}], project_root=team,
                          team_check=False)
    if state == 'stale':
        original = chat_convs.load_conversation
        monkeypatch.setattr(chat_convs, 'load_conversation', lambda cid, project_root=None:
                            None if cid == 'coord' else original(cid, project_root))
    if state == 'ambiguous':
        plans.create_plan('outsider', nodes=[{'id': 'other', 'content': 'Other', 'assignee': 'group-a'}], project_root=team)
    if state == 'other_project':
        root = str(tmp_path / 'other')
    monkeypatch.setattr(panel, '_project_root', lambda: root)
    before = plans.load_plan('builder', root)
    result = json.loads(panel.ducky_get_plan())
    from backend.agent import prompt, chat_title
    from backend.mcp_plugins import epic
    monkeypatch.setattr(epic, 'probe_epic_mcp', lambda **_k: {})
    monkeypatch.setattr(prompt, '_rules_body', lambda *a, **kw: '')
    monkeypatch.setattr(chat_title, 'self_naming_instruction', lambda *a: '')
    runtime = prompt.get_system_prompt_parts(listener_online=False, listener_port=0,
        project_root=root, conv_id='builder')['plan']
    # The database keeps plans with their duckies, so another project finds them too.
    if state in ('assigned', 'archived', 'own') or (state == 'other_project' and backend == 'db'):
        # Open steps in the team's plan outrank a personal plan (Oct 12 2026).
        assert result['plan']['assigned_from']['node_id'] == 'a'
        assert result['plan']['id'] == plans.load_plan('coord', team)['id']
        assert result['plan']['id'] in runtime and '`a1`' in runtime
        assert 'tick_now' not in result  # read view must not imply implicit member writes
        full = json.loads(panel.ducky_get_plan(chat_id='coord'))['plan']
        assert [n['id'] for n in full['nodes']] == ['a', 'b']
        plans.update_node('coord', 'a2', assignee='builder', project_root=team)
        resumed = json.loads(panel.ducky_get_plan())['plan']
        assert resumed['assigned_from']['node_id'] == 'a2'
        patch_turns(monkeypatch, 'get_active_conv_id', lambda: 'builder')
        monkeypatch.setenv('DUCKY_CONV_ID', 'outsider')
        assert json.loads(panel.ducky_get_plan())['plan'] == resumed
    else:
        assert result['plan'] is None
        assert runtime == ''
        if state == 'ambiguous':
            assert not result['ok'] and 'open steps in 2 plans' in result['error']
    assert plans.load_plan('builder', root) == before
    if state in ('assigned', 'archived'):
        # A member's tick lands in its team's plan; its own record stays untouched.
        plans.update_node('builder', 'a1', status='completed', project_root=team)
        assert plans.load_plan('coord', team)['nodes'][0]['children'][0]['status'] == 'completed'
        assert plans.load_plan('builder', root) == before


@pytest.fixture
def big_plan(team, monkeypatch):
    # Oct 24 2026: every edit sent back the 260-step outline (~25k characters) and reading
    # the plan spilled 263 KB. 80 steps with full 2 KB notes make a 150K-character plan.
    from backend.agent import a2a_client
    from backend.tools.panel import ducky_panel
    monkeypatch.setattr(a2a_client, 'send_notice', lambda **kw: None)
    monkeypatch.setattr(ducky_panel, '_project_root', lambda: team)
    for i in range(80):
        plans.add_node('coord', content=f'S{i}', parent_id='a' if i < 40 else 'b',
                       body_markdown=f'Notes {i}. ' + 'Long notes. ' * 170, project_root=team)
    assert len(json.dumps(plans.load_plan('coord', project_root=team))) > 150_000
    return team


def test_plan_edits_reply_with_the_step_and_counts_only(big_plan):
    from backend.tools.panel import ducky_panel as panel
    plans.update_plan('coord', status='paused', project_root=big_plan)  # moving and deleting need a pause
    replies = []
    added = panel.ducky_plan_add_node(content='One more', parent_id='a', body_markdown='Do it. ' * 300,
                                      chat_id='coord')
    replies.append(added)
    node_id = json.loads(added)['node']['id']
    replies.append(panel.ducky_plan_move_node(node_id=node_id, parent_id='a', index=0, chat_id='coord'))
    replies.append(panel.ducky_plan_delete_node(node_id=node_id, chat_id='coord'))
    plans.update_plan('coord', status='active', project_root=big_plan)
    replies.append(panel.ducky_plan_update_node(node_id='a1', status='in_progress', chat_id='coord'))
    for raw in replies:
        out = json.loads(raw)
        assert out['ok'] and len(raw) < 2_000, (len(raw), raw[:200])
        assert 'outline' not in out and 'nodes' not in out['plan'] and 'children' not in out['node']
        assert out['progress']['total'] >= 80 and 'next_tick' in out  # None while paused
    assert json.loads(replies[-1])['next_tick']['call']
    assert json.loads(replies[0])['node']['body_markdown'].startswith('Do it.')
    assert json.loads(replies[-1])['node']['status'] == 'in_progress'


def test_get_plan_outline_and_section_views(big_plan):
    from backend.tools.panel import ducky_panel as panel
    plans.update_node('coord', 'a', assignee='group-a', project_root=big_plan)
    raw = panel.ducky_get_plan(chat_id='coord', outline=True)
    out = json.loads(raw)
    assert len(raw) < 8_000 and 'nodes' not in out['plan']
    assert out['outline'][0]['id'] == 'a' and out['outline'][0]['owner'] == 'group-a'
    assert len(out['outline']) >= 84 and all('body_markdown' not in row for row in out['outline'])
    section = json.loads(panel.ducky_get_plan(chat_id='coord', section='a'))
    ids = [n['id'] for n in section['section']['children']]
    assert section['ok'] and section['n'] == '1' and ids[:2] == ['a1', 'a2'] and len(ids) == 42
    assert all('Long notes.' in n['body_markdown'] for n in section['section']['children'][2:])
    assert 'S40' not in json.dumps(section) and section['progress']['total'] == 43
    assert not json.loads(panel.ducky_get_plan(chat_id='coord', section='nope'))['ok']


def test_update_node_sets_planned_assignee_and_starts_nobody(team, monkeypatch):
    from backend.tools.panel import ducky_panel as panel
    from frontend.ui_web.agent_turns.testing import patch_turns
    pushes = []
    patch_turns(monkeypatch, '_resolve_push', lambda _: pushes.append)
    monkeypatch.setattr(panel, '_project_root', lambda: team)
    out = json.loads(panel.ducky_plan_update_node(node_id='a', planned_assignee='swarm-speed-developer',
                                                  chat_id='coord'))
    assert out['ok'] and out['node']['planned_assignee'] == 'swarm-speed-developer'
    node = plans.load_plan('coord', team)['nodes'][0]
    assert node['planned_assignee'] == 'swarm-speed-developer' and not node.get('assignee')
    plans.drain_after_save()
    assert not [p for p in pushes if p.get('type') == 'plan_assignment_changed']
    json.loads(panel.ducky_plan_update_node(node_id='a', planned_assignee='', chat_id='coord'))
    assert 'planned_assignee' not in plans.load_plan('coord', team)['nodes'][0]


def test_runtime_prompt_refreshes_assignment(team, monkeypatch):
    from backend.agent import prompt, chat_title
    from backend.agent.prompt_cache import build_cache_payload
    from backend.mcp_plugins import epic
    monkeypatch.setattr(epic, 'probe_epic_mcp', lambda **_k: {})
    monkeypatch.setattr(prompt, '_rules_body', lambda *a, **kw: '')
    monkeypatch.setattr(chat_title, 'self_naming_instruction', lambda *a: '')
    plans.update_node('coord', 'a', assignee='group-a', project_root=team)
    conv = SimpleNamespace(prompt_cache_snapshot=None)
    def read():
        parts = prompt.get_system_prompt_parts(listener_online=False, listener_port=0,
            project_root=team, conv_id='builder')
        cached = build_cache_payload(conv, parts, omit=frozenset(), enable_cache=True,
                                    freeze_enabled=True, prompt_cache_key='builder')
        assert parts['plan'] not in cached.frozen_system if parts['plan'] else True
        assert parts['plan'] in cached.dynamic_system
        return parts['plan']
    first = read()
    assert 'a1' in first and 'coord' in first
    assert 'ducky_get_plan' in first and 'ONE plan' in first and 'no chat_id needed' in first
    plans.update_node('coord', 'a2', assignee='builder', project_root=team)
    resumed = read()
    assert '`a2`' in resumed and '`a1`' not in resumed
    assert plans.load_plan('builder', team) is None
