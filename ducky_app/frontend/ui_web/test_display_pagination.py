"""Display reads stay partial, account scoped and separate from agent context."""
import base64
from types import SimpleNamespace

from backend.store import db
from backend.store.repos import chat_display, chats
from frontend.ui_web import chat_rows
from frontend.ui_web.chats import conversations as pc, saves
from frontend.ui_web.panel_api import PanelApi


def api():
    obj = PanelApi.__new__(PanelApi)
    obj.list_running_agents = lambda: []
    return obj


def test_first_page_only_opens_needed_messages_and_full_context_is_intact(monkeypatch):
    messages = [{'role': 'user', 'text': str(i)} for i in range(10000)]
    chats.conv_save('_no_project', {'id': 'paged'}, messages=messages)
    opened = []
    original = chats.messages_range
    def read(conv_id, start, stop=None):
        got, count = original(conv_id, start, stop)
        opened.extend(seq for seq, _ in got)
        return got, count
    monkeypatch.setattr(chats, 'messages_range', read)
    rows = api().load_messages('paged', limit=50)
    # The page reads 16, then widens to 64 messages for its 50 rows: 80 decoded, never the 10,000.
    assert len(set(opened)) <= 64 and len(opened) <= 80
    assert [r['text'] for r in rows] == [str(i) for i in range(9950, 10000)]
    assert len(pc.load_conversation('paged').messages) == 10000
    sql = db.connect().execute(
        'EXPLAIN QUERY PLAN SELECT seq, body, fmt FROM messages WHERE conv_id=? AND seq<=? ORDER BY seq DESC LIMIT 32',
        ('paged', 10000)).fetchall()
    assert any('INDEX' in str(tuple(row)) for row in sql)


def test_large_single_turn_pages_do_not_open_offscreen_parts():
    blocks = [{'type': 'tool_call', 'id': f't{i}', 'name': 'read', 'status': 'success',
               'arguments': {}, 'file_edits': [], 'result': {'data': f'{i}:' + 'x' * 12000}}
              for i in range(600)]
    messages = [{'role': 'assistant', 'blocks': blocks, 'content': 'done'}, {'role': 'user', 'text': 'next'}]
    chats.conv_save('_no_project', {'id': 'many-tools'}, messages=messages)
    full = chat_rows._messages_to_ui(SimpleNamespace(id='many-tools', messages=messages), project_root='')
    assert len({r['id'] for r in full}) == len(full) == 1202
    chats.forget_parts()
    queries = []
    db.connect().set_trace_callback(queries.append)
    try:
        page = api().load_messages('many-tools', limit=50, full_results=True)
        assert page == full[-50:]
        assert sum('SELECT data FROM message_parts' in q for q in queries) <= 25
        older = api().load_messages('many-tools', before_id=page[0]['id'], limit=50, full_results=True)
        assert older == full[-100:-50]
        queries.clear()
        chats.forget_parts()
        result = api().load_tool_result('many-tools', full[1111]['id'])
        assert result == {'ok': True, 'result': full[1111]['tool']['result']}
        assert sum('SELECT data FROM message_parts' in q for q in queries) == 1
    finally:
        db.connect().set_trace_callback(None)


def test_read_snapshot_survives_concurrent_writer():
    import threading
    chats.conv_save('_no_project', {'id': 'snapshot'}, messages=[{'role': 'user', 'text': str(i)} for i in range(70)])
    ready, go, written = threading.Event(), threading.Event(), threading.Event()
    errors = []
    def write():
        try:
            from backend.store import account_rows
            account_rows.ready(account_rows.current())
            db.connect()
            ready.set()
            assert go.wait(10)
            chats.conv_save('_no_project', {'id': 'snapshot'}, messages=[{'role': 'user', 'text': 'new'}])
            written.set()
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close_thread_connections()
    worker = threading.Thread(target=write)
    worker.start()
    assert ready.wait(10)
    reader = chat_display.display_messages('snapshot')
    assert next(reader)[2]['text'] == '69'
    go.set()
    assert written.wait(5)
    rest = [m['text'] for _, _, m in reader]
    worker.join(5)  # test teardown's TRUNCATE waits for readers, unlike the write
    assert not worker.is_alive() and not errors
    assert rest == [str(i) for i in range(68, -1, -1)]
    assert api().load_messages('snapshot', limit=50)[0]['text'] == 'new'


def test_display_image_uses_url_but_provider_and_resend_receive_original(tmp_path, monkeypatch):
    from frontend.ui_web import conversation_attachments as att
    from backend.agent.attachments import parse_attachment_dict
    folder = tmp_path / 'image-chat' / 'attachments'
    folder.mkdir(parents=True)
    data = b'image-payload' * 1000
    (folder / 'test.png').write_bytes(data)
    raw = {'kind': 'image', 'name': 'test.png', 'path': 'attachments/test.png'}
    monkeypatch.setattr(att, 'chat_attachments_dir', lambda cid: folder)
    light = att.hydrate_attachment_dict(raw, 'image-chat', tmp_path, display=True)
    assert 'data_base64' not in light and '/image-chat/test.png' in light['media_url']
    assert base64.b64decode(parse_attachment_dict(light, current=True).data_base64) == data
    full = att.hydrate_attachment_dict(raw, 'image-chat', tmp_path)
    assert base64.b64decode(full['data_base64']) == data
    monkeypatch.setattr(att, '_signed_in_account_may_open', lambda cid: False)
    import pytest
    with pytest.raises(ValueError):
        parse_attachment_dict(light, current=True)


def test_page_keeps_live_checkpoint_and_flushes_queued_save(monkeypatch):
    from frontend.ui_web import live_agent_runs
    conv = pc.create_conversation(title='checkpoint')
    conv.messages = [{'role': 'user', 'content': 'go'},
                     {'role': 'assistant', 'content': 'working', 'incomplete': True, 'run_id': 'r1',
                      'blocks': [{'type': 'tool_call', 'id': 't1', 'name': 'read', 'status': 'pending'}]}]
    saves.save_conversation(conv)
    monkeypatch.setattr(live_agent_runs, 'get_live_run_ids', lambda: frozenset({'r1'}))
    obj = api()
    obj.list_running_agents = lambda: [conv.id]
    rows = obj.load_messages(conv.id, limit=50)
    assert rows[-1]['live_run'] and not rows[-1].get('incomplete')
    assert [r['role'] for r in rows] == ['user', 'tool', 'assistant']
    assert len(pc.load_conversation(conv.id).messages) == 2


def test_display_does_not_read_another_accounts_messages(monkeypatch):
    from backend.store import account_rows
    chats.conv_save('_no_project', {'id': 'private'}, messages=[{'role': 'user', 'text': 'private'}])
    monkeypatch.setattr(account_rows, 'current', lambda: 'different-account')
    monkeypatch.setattr(chat_display, '_display_message', lambda *a: (_ for _ in ()).throw(AssertionError('decoded private row')))
    assert list(chat_display.display_messages('private')) == []
