"""Representative two-group workflow using persisted plans and the real broker."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from backend.agent.coding_agents import plans
from backend.agent.coding_agents.test_plan_assignment import team
from backend.agent.coding_agents.test_plan_concurrency import race
from frontend.ui_web import project_chats


@pytest.mark.parametrize('backend', ['files', 'db'])
def test_two_group_shared_plan_workflow(team, monkeypatch, backend, tmp_path):
    from backend.agent import a2a_broker as broker
    from backend.tools.panel import ducky_panel as panel
    from frontend.ui_web import agent_modes

    monkeypatch.setenv('DUCKY_STORE_BACKEND_PLANS', backend)
    root = team
    parents = {'coord': 'team', 'builder': 'group-a', 'peer': 'group-b',
               'group-a': 'team', 'group-b': 'team', 'team': ''}
    chats = {cid: SimpleNamespace(id=cid, parent_conv_id=parent, title=cid,
        ducky_name=cid, coding_agent='ducky', messages=[], is_group=False)
        for cid, parent in parents.items()}
    monkeypatch.setattr(project_chats, 'load_conversation', lambda cid, project_root=None: chats.get(cid))
    monkeypatch.setattr(project_chats, 'iter_conversations_by_project', lambda: [])
    monkeypatch.setattr(plans, '_push_assignment_invalidated', lambda *args: None)
    monkeypatch.setattr(panel, '_project_root', lambda: root)
    monkeypatch.setattr(agent_modes, 'get_active_conv_id', lambda: 'builder')
    master = plans.update_node('coord', 'a', assignee='group-a', project_root=root)
    plans.update_node('coord', 'b', assignee='group-b', project_root=root)

    def read(cid=None):
        return json.loads(panel.ducky_get_plan(chat_id=cid or ''))['plan']

    assert read()['assigned_from']['node_id'] == 'a'
    assert read('peer')['assigned_from']['node_id'] == 'b'
    assert read()['id'] == read('peer')['id'] == master['id']
    assert [n['id'] for n in read('coord')['nodes']] == ['a', 'b']
    # A scoped read is not a mutation redirect or a cloned personal plan.
    with pytest.raises(ValueError, match='plan not found'):
        plans.update_node('builder', 'a1', status='completed', project_root=root)
    assert plans.load_plan('builder', root) is None
    assert plans.load_plan_view('builder', str(tmp_path / 'other-project')) is None

    race(monkeypatch,
         lambda: plans.update_node('coord', 'a1', status='completed', project_root=root),
         lambda: plans.update_node('coord', 'b', status='completed', project_root=root))
    canonical = read('coord')
    assert plans._walk_find(canonical['nodes'], 'a1')[2]['status'] == 'completed'
    assert plans._walk_find(canonical['nodes'], 'b')[2]['status'] == 'completed'

    # Reuse broker queues/formatting; only ultimate runtime delivery is fake.
    for name in ('_inbox', '_ring', '_threads', '_active', '_held', '_cooldowns', '_timers'):
        monkeypatch.setattr(broker, name, {})
    for name in ('_delivering', '_delivery_inflight', '_delivery_again', '_stopped',
                 '_completed_runs', '_uncertain_chats'):
        monkeypatch.setattr(broker, name, set())
    sent = []
    def deliver(cid, text, *args, **kwargs):
        sent.append((cid, text))
        return 'synthetic-run-' + cid
    monkeypatch.setattr(agent_modes, 'run_message', deliver)
    monkeypatch.setattr(agent_modes, 'is_agent_running', lambda cid: False)
    monkeypatch.setattr(agent_modes, 'wait_for_idle', lambda *args: True)
    def settle(count):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with broker._lock:
                idle = not broker._delivery_inflight
            if len(sent) == count and idle:
                return
            time.sleep(0.01)
        raise AssertionError('synthetic broker delivery did not settle')

    evidence = f"master={master['id']} node=a1 revision={'a' * 40}"
    outcome = broker.send(sender_conv_id='builder', receiver_conv_id='peer',
                          body=evidence, expect_reply=True)
    settle(1)
    rid = outcome['response_id']
    assert evidence in sent[0][1] and rid in sent[0][1]
    response = evidence + ' PASS: canonical a1 completed'
    broker.send(sender_conv_id='peer', receiver_conv_id='builder', body=response,
                expect_reply=False, response_id=rid)
    settle(2)
    row = broker.read_inbox('builder')[0]
    assert (row['from'], row['body'], row['response_id']) == ('peer', response, rid)
    with pytest.raises(ValueError, match='not open'):
        broker.send(sender_conv_id='peer', receiver_conv_id='builder', body=response,
                    expect_reply=False, response_id=rid)
    assert len(sent) == 2
    assert read('coord') == canonical  # the handoff cannot authorize/change status

    archived = plans.save_plan({'id': 'old-personal', 'chat_id': 'builder',
        'status': 'archived', 'title': 'Historical note', 'nodes': []}, root)
    plans.update_node('coord', 'a2', assignee='builder', project_root=root)
    assigned = read()
    assert assigned['id'] == master['id']
    assert assigned['assigned_from']['node_id'] == 'a2'
    assert plans.load_plan('builder', root) == archived
    # New interpreter, same isolated real store: no inherited assignment cache.
    env = os.environ.copy()
    env['PYTHONPATH'] = str(Path(plans.__file__).resolve().parents[3])
    child = subprocess.run([sys.executable, '-B', '-c', '''
import json, sys
from types import SimpleNamespace
from backend.agent.coding_agents import plans
from frontend.ui_web import project_chats
parents = json.loads(sys.argv[2])
project_chats.load_conversation = lambda cid, project_root=None: (
    SimpleNamespace(parent_conv_id=parents[cid]) if cid in parents else None)
print(json.dumps(plans.load_plan_view('builder', sys.argv[1])))
''', root, json.dumps(parents)], env=env, capture_output=True, text=True, timeout=20, check=True)
    assert json.loads(child.stdout) == assigned
    assert [p['chat_id'] for p in plans.list_plans(root)].count('coord') == 1
