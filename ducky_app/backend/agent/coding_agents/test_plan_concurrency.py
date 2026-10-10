"""Real plan mutations retain concurrent edits in both supported stores."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread

import pytest

from backend.agent.coding_agents import plans


@pytest.fixture(params=['files', 'db'])
def store(request, tmp_path, monkeypatch):
    monkeypatch.setenv('DUCKY_STORE_BACKEND_PLANS', request.param)
    monkeypatch.setattr(plans, '_push_assignment_invalidated', lambda *args: None)
    root = str(tmp_path)
    plans.create_plan('owner', project_root=root, nodes=[
        {'id': 'a', 'content': 'First'}, {'id': 'b', 'content': 'Second'}])
    return root


def race(monkeypatch, first, second):
    """Force stale reads without replacing either API or storage implementation.

    The first read waits briefly for a competing read. With serialization that
    read cannot happen until the first mutation finishes; the wait then expires.
    Without it, the second mutation saves its stale snapshot after the first.
    """
    original = plans.load_plan
    first_read, second_read, first_done = Event(), Event(), Event()

    def read(*args, **kwargs):
        doc = original(*args, **kwargs)
        if current_thread().name.endswith('_0'):
            first_read.set()
            second_read.wait(0.3)
        else:
            second_read.set()
            assert first_done.wait(5)
        return doc

    def run_first():
        try:
            return first()
        finally:
            first_done.set()

    with monkeypatch.context() as patch:
        patch.setattr(plans, 'load_plan', read)
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix='plan-race') as pool:
            one = pool.submit(run_first)
            assert first_read.wait(5)
            two = pool.submit(second)
            return one.result(timeout=10), two.result(timeout=10)


@pytest.mark.parametrize('api', ['node', 'todos'])
def test_different_original_nodes_keep_both_statuses(store, monkeypatch, api):
    def tick(node):
        if api == 'node':
            return plans.update_node('owner', node, status='completed', project_root=store)
        return plans.update_plan('owner', project_root=store, todos=[
            {'id': node, 'content': 'First' if node == 'a' else 'Second', 'status': 'completed'}])
    race(monkeypatch, lambda: tick('a'), lambda: tick('b'))
    doc = plans.load_plan('owner', store)
    assert [(n['id'], n['status']) for n in doc['nodes']] == [('a', 'completed'), ('b', 'completed')]


def test_same_node_independent_fields_survive(store, monkeypatch):
    plans.update_plan('owner', status='paused', project_root=store)
    race(monkeypatch,
         lambda: plans.update_node('owner', 'a', content='Changed', project_root=store),
         lambda: plans.update_node('owner', 'a', body_markdown='Details', project_root=store))
    node = plans.load_plan('owner', store)['nodes'][0]
    assert node['content'] == 'Changed'
    assert node['body_markdown'] == 'Details'


@pytest.mark.parametrize('operation', ['edit', 'add', 'delete', 'move'])
def test_structure_gate_checks_latest_started_state(store, monkeypatch, operation):
    def mutate():
        try:
            if operation == 'edit':
                plans.update_node('owner', 'b', content='Changed', project_root=store)
            elif operation == 'add':
                plans.add_node('owner', content='Extra', project_root=store)
            elif operation == 'delete':
                plans.delete_node('owner', 'b', project_root=store)
            else:
                plans.move_node('owner', 'b', index=0, project_root=store)
        except ValueError as exc:
            return str(exc)
        return ''
    _, refusal = race(monkeypatch,
        lambda: plans.update_node('owner', 'a', status='in_progress', project_root=store), mutate)
    assert refusal == 'plan is playing — pause it to edit unfinished steps or add new ones'
    assert [(n['id'], n['status']) for n in plans.load_plan('owner', store)['nodes']] == [
        ('a', 'in_progress'), ('b', 'pending')]
