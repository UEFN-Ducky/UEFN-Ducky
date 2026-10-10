"""Regression replay for report loss and repeat delivery across process boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from backend.agent.test_a2a_broker import broker, _wait_sent  # noqa: F401


@pytest.fixture
def reports(broker, monkeypatch):  # noqa: F811 - imported pytest fixture
    b = broker.mod
    timers = []

    class Timer:
        def __init__(self, delay, callback, args=()):
            self.delay, self.callback, self.args = delay, callback, args
            self.cancelled = False
            timers.append(self)
        def start(self):
            pass
        def cancel(self):
            self.cancelled = True
        def fire(self):
            if not self.cancelled:
                self.callback(*self.args)

    monkeypatch.setattr(b.threading, 'Timer', Timer)
    notices = []
    monkeypatch.setattr(b, '_owner_report_notice', lambda cid, rs: notices.append((cid, [r.envelope.response_id for r in rs])))
    broker.timers, broker.notices = timers, notices
    return broker


def _settle(b):
    deadline = time.monotonic() + 3
    while b._delivery_inflight and time.monotonic() < deadline:
        time.sleep(.005)
    assert not b._delivery_inflight


def _deliver_reports(f, count=3):
    b = f.mod
    # Coordinator is running an owner turn while three builders report.
    b._active['coord'] = {'envelopes': []}
    for n in range(count):
        rid = b.open_thread('coord', f'member{n}')
        b.send(sender_conv_id=f'member{n}', receiver_conv_id='coord',
               body=f'FULL REPORT {n}\ncommit abc{n}\nNo reply needed', expect_reply=False, response_id=rid)
    b.send_notice(sender_conv_id='owner', receiver_conv_id='coord', body='Owner note: No reply needed')
    b.on_agent_stopped('coord', 'done', run_id='owner-turn')
    _wait_sent(f.modes)
    _settle(b)
    assert len(b.unanswered_reports('coord')) == count


def test_oct10_empty_turn_repeats_only_unacted_reports_and_notifies_once(reports):
    f, b = reports, reports.mod
    _deliver_reports(f)
    assert 'Owner note: No reply needed' in f.modes.sent[0][1]
    b.on_agent_stopped('coord', 'done', run_id='empty0')
    assert 0 < f.timers[-1].delay <= 60
    f.timers[-1].fire()
    _wait_sent(f.modes, 2)
    _settle(b)
    assert f.modes.sent[-1][1].startswith('Reports you have not acted on')
    for n in range(3):
        assert f'FULL REPORT {n}\ncommit abc{n}\nNo reply needed' in f.modes.sent[-1][1]
    b.send(sender_conv_id='coord', receiver_conv_id='member0', body='Next task', expect_reply=False)
    _settle(b)
    b.on_agent_stopped('coord', 'done', run_id='empty1')
    for i in (2, 3):
        before = len(f.modes.sent)
        f.timers[-1].fire()
        _wait_sent(f.modes, before + 1)
        _settle(b)
        text = f.modes.sent[-1][1]
        assert 'FULL REPORT 0' not in text
        assert 'FULL REPORT 1' in text and 'FULL REPORT 2' in text
        b.on_agent_stopped('coord', 'done', run_id=f'empty{i}')
    assert len(f.notices) == 1 and len(f.notices[0][1]) == 2
    f.timers[-1].fire()
    _settle(b)
    b.on_agent_stopped('coord', 'done', run_id='empty4')
    assert len(f.notices) == 1


def test_answered_in_same_turn_never_repeats(reports):
    _deliver_reports(reports, 1)
    b = reports.mod
    b.send(sender_conv_id='coord', receiver_conv_id='member0', body='Proceed', expect_reply=False)
    _settle(b)
    b.on_agent_stopped('coord', 'done')
    assert not b.unanswered_reports('coord')
    assert not reports.timers


def test_plan_action_acknowledges_only_that_member(reports):
    _deliver_reports(reports)
    reports.mod.acknowledge_reports('coord', 'member1')
    assert {r['from'] for r in reports.mod.unanswered_reports('coord')} == {'member0', 'member2'}


def test_ack_after_timer_queued_drops_stale_redelivery(reports):
    _deliver_reports(reports, 1)
    b = reports.mod
    b.on_agent_stopped('coord', 'done')
    # Simulate the coordinator acting before the callback gets a delivery slot.
    b.acknowledge_reports('coord', 'member0')
    reports.timers[-1].fire()
    assert len(reports.modes.sent) == 1


@pytest.mark.parametrize('reply', [False, True])
def test_two_tool_server_messages_both_start_window_turns(reports, tmp_path, reply):
    from frontend.ui_web.panel_api_agents import PanelApiAgentsMixin
    f, b = reports, reports.mod
    api = PanelApiAgentsMixin()
    ids = [b.open_thread('coord', 'member', response_id=f'report{i}') for i in range(2)] if reply else ['', '']
    # open_thread coalesces a pair; close/reopen is done by the test server after each turn.
    if reply:
        ids = ['report0', 'report1']
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path == '/end-turn':
                _settle(b)
                b.on_agent_stopped('coord', 'done')
                if reply:
                    b.open_thread('coord', 'member', response_id='report1')
                result = {'ok': True}
            else:
                result = {'ok': True, 'result': api.agent_broker_call(**payload['args'])}
            data = json.dumps(result).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    script = tmp_path / 'outside.py'
    script.write_text('''import json, sys, urllib.request
from types import SimpleNamespace
from backend.tools.panel import ducky_panel as t
from backend.agent import a2a_broker as b
from frontend.ui_web import agent_modes
from frontend import settings
settings.PANEL_LISTENER_PORT = int(sys.argv[1]) + 1
agent_modes.get_panel_push = lambda: None
t._resolve_sender = lambda value: value
t._project_root = lambda: ''
t.load_conversation = lambda *a, **kw: SimpleNamespace(folder_id='')
b._kick_delivery = lambda *a: (_ for _ in ()).throw(AssertionError('tool-server broker used'))
for rid in json.loads(sys.argv[2]):
    result = json.loads(t.ducky_agent_send(to='coord', sender='member', message='report or assignment', expect_reply=False, response_id=rid))
    assert result['status'] == 'sent'
    request = urllib.request.Request('http://127.0.0.1:' + sys.argv[1] + '/end-turn', data=b'{}', method='POST')
    urllib.request.urlopen(request, timeout=10).read()
assert not b._active and not b._inbox and not b._threads
''', encoding='utf-8')
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    try:
        proc = subprocess.run([sys.executable, str(script), str(server.server_port), json.dumps(ids)], env=env, capture_output=True, text=True, timeout=35)
        assert proc.returncode == 0, proc.stderr
        _wait_sent(f.modes, 2)
        assert len(f.modes.sent) == 2
        assert all(cid == 'coord' for cid, _ in f.modes.sent)
    finally:
        server.shutdown()
        server.server_close()

def test_owner_notice_is_persisted_in_coordinator_chat(broker, monkeypatch):  # noqa: F811
    b, chats, modes = broker.mod, broker.chats, broker.modes
    saved, changed = [], []
    monkeypatch.setattr(chats, 'append_message', lambda conv, msg: conv.messages.append(msg), raising=False)
    monkeypatch.setattr(chats, 'save_conversation', lambda conv: saved.append(conv), raising=False)
    monkeypatch.setattr(modes, 'notify_context_changed', changed.append, raising=False)
    report = b.UnansweredReport(b.Envelope('recv1', 'sender1', 'Done', response_id='r17', is_report=True), redeliveries=3)
    b._owner_report_notice('sender1', [report])
    assert saved[0].messages[-1]['role'] == 'assistant'
    assert 'Builder (r17)' in saved[0].messages[-1]['content']
    assert changed == ['sender1']


def test_busy_coordinator_retries_after_becoming_idle(reports):
    _deliver_reports(reports, 1)
    b = reports.mod
    b.on_agent_stopped('coord', 'done')
    reports.modes.running.add('coord')
    reports.timers[-1].fire()
    assert len(reports.modes.sent) == 1
    reports.modes.running.clear()
    reports.timers[-1].fire()
    _wait_sent(reports.modes, 2)
    _settle(b)
    assert 'FULL REPORT 0' in reports.modes.sent[-1][1]

def test_actual_plan_change_acknowledges_inherited_group_owner_only(reports, monkeypatch):
    import copy
    from backend.agent.coding_agents import plans
    _deliver_reports(reports)
    b = reports.mod
    monkeypatch.setattr(plans, '_chat_and_group_ids', lambda member, root: {member, 'group1' if member == 'member1' else 'other'})
    before = {'chat_id':'coord', 'nodes':[{'id':'section', 'assignee':'group1', 'children':[{'id':'step', 'status':'pending'}]}]}
    after = copy.deepcopy(before)
    after['nodes'][0]['children'][0]['status'] = 'in_progress'
    b.acknowledge_plan_changes(before, before, 'coord')
    assert len(b.unanswered_reports('coord')) == 3
    b.acknowledge_plan_changes(before, after, 'member1')
    assert len(b.unanswered_reports('coord')) == 3
    b.acknowledge_plan_changes(before, after, 'coord')
    assert {r['from'] for r in b.unanswered_reports('coord')} == {'member0', 'member2'}

def test_saved_plan_hook_uses_real_actor_and_only_successful_save(monkeypatch, tmp_path):
    from backend.agent import a2a_client
    from backend.workspace import identity
    from types import SimpleNamespace
    before = {'chat_id':'coord','nodes':[{'id':'s','status':'pending'}]}
    after = {'chat_id':'coord','nodes':[{'id':'s','status':'in_progress'}]}
    calls = []
    monkeypatch.setattr(identity, 'resolve_context', lambda: SimpleNamespace(conv_id='coord'))
    monkeypatch.setattr(a2a_client, '_call', lambda *a: calls.append(a))
    a2a_client.acknowledge_saved_plan(before, after, str(tmp_path))
    assert calls[0] == ('acknowledge_plan_changes', before, after, 'coord', str(tmp_path))
    calls.clear()
    monkeypatch.setattr(identity, 'resolve_context', lambda: SimpleNamespace(conv_id='member'))
    a2a_client.acknowledge_saved_plan(before, after)
    assert not calls


def test_answering_a_redelivered_report_on_its_expired_thread_acknowledges_it(reports):
    # Oct 10 2026: the report told the coordinator to answer with its response_id; that
    # thread had closed, every answer failed "response_id is not open", and the report
    # came back every half minute after the plan was already finished.
    _deliver_reports(reports, 1)
    b = reports.mod
    rid = b.unanswered_reports('coord')[0]['response_id']
    assert rid not in b._threads
    out = b.send(sender_conv_id='coord', receiver_conv_id='member0', body='Got it, all done', expect_reply=False,
                 response_id=rid)
    assert out['closed_thread'] is False
    _settle(b)
    assert not b.unanswered_reports('coord')
    with pytest.raises(ValueError, match='not open'):
        b.send(sender_conv_id='coord', receiver_conv_id='member0', body='again', expect_reply=False, response_id=rid)
    with pytest.raises(ValueError, match='not open'):
        b.send(sender_conv_id='coord', receiver_conv_id='member9', body='x', expect_reply=False, response_id='nope')


def test_reports_stop_coming_back_once_the_owner_is_told(reports):
    f, b = reports, reports.mod
    _deliver_reports(f, 1)
    b.on_agent_stopped('coord', 'done', run_id='empty0')
    for i in (1, 2, 3):
        before = len(f.modes.sent)
        f.timers[-1].fire()
        _wait_sent(f.modes, before + 1)
        _settle(b)
        b.on_agent_stopped('coord', 'done', run_id=f'empty{i}')
    assert len(f.notices) == 1
    assert not b.unanswered_reports('coord')
    sent = len(f.modes.sent)
    for timer in list(f.timers):
        timer.fire()
    _settle(b)
    assert len(f.modes.sent) == sent
