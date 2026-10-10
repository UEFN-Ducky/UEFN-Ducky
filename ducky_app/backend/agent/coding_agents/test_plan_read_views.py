"""Canonical assignment reads through MCP and runtime prompt boundaries."""
import json
from types import SimpleNamespace

import pytest
from backend.agent.coding_agents import plans
from backend.agent.coding_agents.test_plan_assignment import team
from frontend.ui_web import project_chats


@pytest.mark.parametrize('backend', ['files', 'db'])
@pytest.mark.parametrize('state', ['assigned', 'archived', 'own', 'missing', 'stale', 'ambiguous', 'other_project'])
def test_mcp_assignment_read_preserves_raw_ownership(team, monkeypatch, backend, state, tmp_path):
    from backend.tools.panel import ducky_panel as panel
    from frontend.ui_web import agent_modes
    monkeypatch.setenv('DUCKY_STORE_BACKEND_PLANS', backend)
    monkeypatch.setattr(agent_modes, 'get_active_conv_id', lambda: '')
    monkeypatch.setenv('DUCKY_CONV_ID', 'builder')
    root = team
    if state != 'missing':
        plans.update_node('coord', 'a', assignee='group-a', project_root=team)
    if state == 'archived':
        plans.save_plan({'id': 'history', 'chat_id': 'builder', 'status': 'archived', 'nodes': []}, team)
    if state == 'own':
        plans.create_plan('builder', nodes=[{'id': 'personal', 'content': 'Personal'}], project_root=team)
    if state == 'stale':
        original = project_chats.load_conversation
        monkeypatch.setattr(project_chats, 'load_conversation', lambda cid, project_root=None:
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
    monkeypatch.setattr(epic, 'probe_epic_mcp', lambda: {})
    monkeypatch.setattr(prompt, '_rules_body', lambda *a, **kw: '')
    monkeypatch.setattr(chat_title, 'self_naming_instruction', lambda *a: '')
    runtime = prompt.get_system_prompt_parts(listener_online=False, listener_port=0,
        project_root=root, conv_id='builder')['plan']
    if state in ('assigned', 'archived'):
        assert result['plan']['assigned_from']['node_id'] == 'a'
        assert result['plan']['id'] == plans.load_plan('coord', team)['id']
        assert result['plan']['id'] in runtime and '`a1`' in runtime
        assert 'tick_now' not in result  # read view must not imply implicit member writes
        full = json.loads(panel.ducky_get_plan(chat_id='coord'))['plan']
        assert [n['id'] for n in full['nodes']] == ['a', 'b']
        plans.update_node('coord', 'a2', assignee='builder', project_root=team)
        resumed = json.loads(panel.ducky_get_plan())['plan']
        assert resumed['assigned_from']['node_id'] == 'a2'
        monkeypatch.setattr(agent_modes, 'get_active_conv_id', lambda: 'builder')
        monkeypatch.setenv('DUCKY_CONV_ID', 'outsider')
        assert json.loads(panel.ducky_get_plan())['plan'] == resumed
    elif state == 'own':
        assert result['plan']['nodes'][0]['id'] == 'personal'
        assert 'assigned_from' not in result['plan']
        assert '`personal`' in runtime
    else:
        assert result['plan'] is None
        assert runtime == ''
        if state == 'ambiguous':
            assert not result['ok'] and 'Multiple plan assignments' in result['error']
    assert plans.load_plan('builder', root) == before
    if state in ('assigned', 'archived'):
        with pytest.raises(ValueError):
            plans.update_node('builder', 'a1', status='completed', project_root=team)
        assert plans.load_plan('coord', team)['nodes'][0]['children'][0]['status'] == 'in_progress'


def test_runtime_prompt_refreshes_assignment_without_mutation_redirect(team, monkeypatch):
    from backend.agent import prompt, chat_title
    from backend.agent.prompt_cache import build_cache_payload
    from backend.mcp_plugins import epic
    monkeypatch.setattr(epic, 'probe_epic_mcp', lambda: {})
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
    assert 'ducky_get_plan' in first and 'read-only' in first
    plans.update_node('coord', 'a2', assignee='builder', project_root=team)
    resumed = read()
    assert '`a2`' in resumed and '`a1`' not in resumed
    assert plans.load_plan('builder', team) is None
