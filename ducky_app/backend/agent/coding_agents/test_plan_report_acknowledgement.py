"""Successful coordinator plan edits acknowledge reports through the window broker."""
from types import SimpleNamespace

import pytest

from backend.agent.coding_agents.test_plan_assignment import team  # noqa: F401
from backend.agent.coding_agents import plans
from backend.agent import a2a_client
from backend.workspace import identity


def test_committed_edit_acknowledges_and_refused_edit_does_not(team, monkeypatch):  # noqa: F811
    calls = []
    monkeypatch.setattr(identity, 'resolve_context', lambda: SimpleNamespace(conv_id='coord', ducky_name='Coordinator'))
    monkeypatch.setattr(a2a_client, '_call', lambda *a, **kw: calls.append((a, kw)))
    plans.update_node('coord', 'a', assignee='group-a', project_root=team)
    calls.clear()
    plans.update_node('coord', 'a2', status='in_progress', project_root=team)
    ack = [args for args, _ in calls if args[0] == 'acknowledge_plan_changes']
    assert len(ack) == 1
    _, before, after, actor, root = ack[0]
    assert plans._walk_find(before['nodes'], 'a2')[2]['status'] == 'pending'
    assert plans._walk_find(after['nodes'], 'a2')[2]['status'] == 'in_progress'
    assert plans.load_plan('coord', team)['nodes'] == after['nodes']
    assert actor == 'coord' and root == team
    calls.clear()
    with pytest.raises(ValueError, match='assignee'):
        plans.update_node('coord', 'a', assignee='missing', project_root=team)
    assert not calls
