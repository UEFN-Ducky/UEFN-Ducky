import time
from backend.bridge import client


def test_response_cache_limits_and_idle_expiry(monkeypatch):
    monkeypatch.setattr(client, '_response_cache', {})
    monkeypatch.setattr(client, '_CACHE_MAX_ENTRIES', 4)
    monkeypatch.setattr(client, '_CACHE_MAX_BYTES', 8000)
    for n in range(30):
        client._remember_response(('list_assets', str(n)), {'data': 'x' * 200}, .05)
    assert len(client._response_cache) <= 4
    assert sum(v[2] for v in client._response_cache.values()) <= 8000
    client._remember_response(('list_assets', 'huge'), {'data': 'x' * 9000}, 30)
    assert ('list_assets', 'huge') not in client._response_cache
    deadline = time.monotonic() + 2
    while client._response_cache and time.monotonic() < deadline:
        time.sleep(.01)
    assert not client._response_cache


def test_a_short_entry_expires_on_time_behind_a_long_pending_timer(monkeypatch):
    monkeypatch.setattr(client, '_response_cache', {})
    monkeypatch.setattr(client, '_cache_timer', None)
    client._remember_response(('get_project_info', ''), {'a': 1}, 30)
    client._remember_response(('list_assets', 'short'), {'a': 2}, .05)
    deadline = time.monotonic() + 2
    while ('list_assets', 'short') in client._response_cache and time.monotonic() < deadline:
        time.sleep(.01)
    assert ('list_assets', 'short') not in client._response_cache
    assert ('get_project_info', '') in client._response_cache
    client._cache_timer.cancel()


def test_a_write_keeps_project_info_and_the_trace_splits_queue_from_editor_time(monkeypatch):
    """Nov 16 2026: get_project_info took 8.1 s (every write dropped its cached answer, so it
    queued on the game thread again), and build_widget_tree read 11.7 s with 2.0 s of editor work."""
    posts, traced = [], []
    monkeypatch.setattr(client, '_response_cache', {})
    monkeypatch.setattr(client, 'configured_listener_port', lambda: 4200)
    monkeypatch.setattr(client, '_island_lock_refusal', lambda *a: None)
    monkeypatch.setattr(client, '_record_editor_change', lambda *a, **k: None)

    def post(port, command, params, timeout):
        posts.append(command)
        time.sleep(.05)
        return {'success': True, 'result': {'cmd': command}, 'ms': 10.0}

    monkeypatch.setattr(client, '_post_json_locked', post)
    monkeypatch.setattr('frontend.perf_trace.trace', lambda kind, name, ms, **meta: traced.append((name, meta)))
    for command in ('get_project_info', 'get_level_info', 'spawn_actor', 'get_project_info', 'get_level_info'):
        client._send_command(command, {})
    assert posts == ['get_project_info', 'get_level_info', 'spawn_actor', 'get_level_info']
    assert traced[0][1]['editor_ms'] == 10.0 and traced[0][1]['queue_ms'] >= 30


def test_an_auto_exposed_command_called_without_params_says_where_they_go(monkeypatch):
    import pytest
    from backend.bridge import dynamic_tools

    def refused(command, params):
        raise RuntimeError("UEFN command 'spawn_layout' failed: cmd_spawn_layout() missing 1 required positional argument: 'path'\nTraceback…")

    monkeypatch.setattr('backend.bridge.send_command', refused)
    tool = dynamic_tools._make_passthrough('spawn_layout', '', [{'name': 'path'}, {'name': 'undo_tag'}])
    with pytest.raises(ValueError, match=r'put them inside `params`.*keys: path, undo_tag'):
        tool()
