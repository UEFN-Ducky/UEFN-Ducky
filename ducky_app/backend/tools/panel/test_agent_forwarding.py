"""The window broker is mandatory for calls from external tool processes."""
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.agent import a2a_broker, a2a_client
from frontend.ui_web import agent_modes
from frontend.ui_web.panel_api import PanelApi


@pytest.fixture
def window(monkeypatch):
    monkeypatch.setattr(agent_modes, 'get_panel_push', lambda: None)
    calls = []
    api = PanelApi()
    def urlopen(request, timeout):
        assert request.full_url.endswith('/__panel_api/agent_broker_call')
        args = json.loads(request.data)['args']
        calls.append(args)
        return io.BytesIO(json.dumps({'ok': True, 'result': api.agent_broker_call(**args)}).encode())
    monkeypatch.setattr('urllib.request.urlopen', urlopen)
    return SimpleNamespace(calls=calls, api=api)


@pytest.mark.parametrize('name,args,kwargs,result', [
    ('read_inbox', ('chat',), {}, [{'body':'report'}]),
    ('send_notice', (), {'sender_conv_id':'a','receiver_conv_id':'b','body':'notice'}, None),
    ('open_thread', ('a','b'), {'deliver_result':True}, 'rid'),
    ('close_thread', ('rid',), {}, None),
    ('open_threads_for_receiver', ('b',), {}, []),
    ('stats', (), {}, {'queued':{'b':2}}),
    ('on_agent_stopped', ('b','done'), {}, None),
    ('on_agent_cancelled_by_user', ('b',), {}, None),
    ('acknowledge_reports', ('a','b'), {}, None),
])
def test_every_broker_operation_reaches_window(window, monkeypatch, name, args, kwargs, result):
    fn = Mock(return_value=result)
    monkeypatch.setattr(a2a_broker, name, fn)
    assert getattr(a2a_client, name)(*args, **kwargs) == result
    fn.assert_called_once_with(*args, **kwargs)
    assert window.calls[0]['operation'] == name


def test_window_unavailable_never_queues_locally(monkeypatch):
    monkeypatch.setattr(agent_modes, 'get_panel_push', lambda: None)
    monkeypatch.setattr('urllib.request.urlopen', Mock(side_effect=OSError('offline')))
    local = Mock()
    monkeypatch.setattr(a2a_broker, 'send', local)
    with pytest.raises(RuntimeError, match='not queued locally'):
        a2a_client.send(sender_conv_id='a', receiver_conv_id='b', body='work', expect_reply=False)
    local.assert_not_called()


def test_embedded_agent_calls_window_broker_directly(monkeypatch):
    monkeypatch.setattr(agent_modes, 'get_panel_push', lambda: lambda e: None)
    local = Mock(return_value={'queued_for':'b'})
    monkeypatch.setattr(a2a_broker, 'send', local)
    http = Mock(side_effect=AssertionError('unexpected HTTP'))
    monkeypatch.setattr('urllib.request.urlopen', http)
    assert a2a_client.send(body='hello') == {'queued_for':'b'}
    local.assert_called_once_with(body='hello')


def test_remote_validation_error_is_returned(window, monkeypatch):
    monkeypatch.setattr(a2a_broker, 'send', Mock(side_effect=ValueError('response_id is not open')))
    with pytest.raises(RuntimeError, match='response_id is not open'):
        a2a_client.send(body='hello')


def test_rpc_rejects_private_broker_methods(window):
    assert window.api.agent_broker_call('_kick_delivery', args=['x'])['ok'] is False

@pytest.mark.parametrize('started', ['queued', 'run1', ''])
def test_direct_chat_message_acknowledges_only_when_accepted(monkeypatch, started):
    from backend.tools.panel import ducky_panel
    from backend.workspace import identity
    monkeypatch.setattr(ducky_panel, '_project_root', lambda: '')
    monkeypatch.setattr(ducky_panel, 'load_conversation', lambda *a, **kw: SimpleNamespace(folder_id=''))
    monkeypatch.setattr(identity, 'resolve_context', lambda: SimpleNamespace(conv_id='coord'))
    monkeypatch.setattr(agent_modes, 'run_message', lambda *a, **kw: started)
    ack = Mock()
    monkeypatch.setattr(a2a_client, 'acknowledge_reports', ack)
    ducky_panel.ducky_send_chat_message('member', 'next task', wait_for_reply=False)
    if started:
        ack.assert_called_once_with('coord', 'member')
    else:
        ack.assert_not_called()
