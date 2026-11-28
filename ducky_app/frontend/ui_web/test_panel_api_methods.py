"""Smoke: the PanelApi methods the team screens call keep their arguments and result keys.

The full list of public methods and their parameters is api_contract.json
(test_api_contract.py).
"""

from __future__ import annotations

from frontend.ui_web.panel_api import PanelApi

# Team screens (title-bar menu, Team window, live invite) call these through the bridge.
# name -> exact argument names, in order. Rename or reorder one and sync breaks silently.
_TEAM_SCREEN_API = {
    'live_overview': [],
    'live_team_join': ['account', 'space', 'room'],
    'live_team_invite': ['account', 'space', 'device'],
    'live_team_leave': ['account', 'space'],
    'plugin_team_overview': ['space', 'etag'],
    'plugin_scope_choices': [],
    'plugin_scope_set_all': ['scope_id'],
    'plugin_scope_set': ['plugin_id', 'scope_id', 'options'],
    'plugin_scope_status': ['plugin_id'],
    'plugin_scope_sync': ['plugin_id', 'force'],
    'plugin_scope_pull': ['plugin_id', 'kind', 'key'],
    'plugin_scope_history': ['plugin_id', 'request'],
}


def test_team_screen_api_signatures() -> None:
    import inspect

    for name, args in _TEAM_SCREEN_API.items():
        fn = getattr(PanelApi, name, None)
        assert callable(fn), f'PanelApi.{name} is gone (team screens call it)'
        got = [p for p in inspect.signature(fn).parameters if p != 'self']
        assert got == args, f'PanelApi.{name} args changed: {got} != {args}'


def test_team_screen_api_result_keys(monkeypatch) -> None:
    """Top-level keys the screens read; failures keep their fallback shape."""
    from backend.uefn_plugins import live, spaces

    def boom(*_a, **_k):
        raise RuntimeError('x')

    api = PanelApi.__new__(PanelApi)

    # live_overview: passes the engine's dict through; on error falls back to an empty menu.
    overview = {'ok': True, 'on': True, 'teams': [{'account': 'a', 'space': 's', 'groups': [
        {'room': 'r', 'members': [{'device': 'd', 'name': 'n', 'me': True}]}]}]}
    monkeypatch.setattr(live, 'overview', lambda: overview)
    assert api.live_overview() is overview
    member = api.live_overview()['teams'][0]['groups'][0]['members'][0]
    assert {'device', 'name', 'me'} <= set(member)
    monkeypatch.setattr(live, 'overview', boom)
    assert {'ok', 'on', 'teams', 'error'} <= set(api.live_overview())

    monkeypatch.setattr(live, 'invite', boom)
    assert {'ok', 'error'} <= set(api.live_team_invite('a', 's', 'd'))

    monkeypatch.setattr(spaces, 'scope_choices', boom)
    assert {'ok', 'choices', 'error'} <= set(api.plugin_scope_choices())

    monkeypatch.setattr(spaces, 'team_overview', boom)
    assert {'ok', 'current', 'label', 'plugins', 'error'} <= set(api.plugin_team_overview('s'))

    monkeypatch.setattr(spaces, 'scope_status', boom)
    assert {'ok', 'visible', 'error'} <= set(api.plugin_scope_status('p'))


def test_big_plugin_call_reply_traced_with_plugin_and_method(monkeypatch) -> None:
    from backend.uefn_plugins.plugin_host.testing import patch_host
    from frontend import perf_trace

    rows: list[tuple] = []
    monkeypatch.setattr(perf_trace, 'trace', lambda kind, name='', ms=0.0, **meta: rows.append((kind, name, meta)))
    api = PanelApi.__new__(PanelApi)

    patch_host(monkeypatch, 'call_panel_rpc', lambda p, m, a: {'ok': True, 'rows': ['x' * 1000] * 100})
    assert api.plugin_call('account', 'list_models', {'_etag': ''})['ok']
    assert len(rows) == 1
    kind, name, meta = rows[0]
    assert kind == 'ui_js_big'
    assert name == meta['source'] == 'plugin_call:account.list_models'
    assert meta['js_bytes'] > 64 * 1024

    rows.clear()
    patch_host(monkeypatch, 'call_panel_rpc', lambda p, m, a: {'ok': True})
    api.plugin_call('account', 'ping', {'_etag': ''})
    assert rows == []  # small replies trace nothing


def test_plugin_call_etag_skips_unchanged_big_reply(monkeypatch) -> None:
    import json

    from backend.uefn_plugins.plugin_host.testing import patch_host

    seen: list[dict] = []
    data = {'ok': True, 'models': [{'id': f'm{i}', 'blob': 'x' * 900} for i in range(2000)]}

    def rpc(p, m, a):
        seen.append(a)
        return data

    patch_host(monkeypatch, 'call_panel_rpc', rpc)
    api = PanelApi.__new__(PanelApi)

    first = api.plugin_call('account', 'list_models', {'_etag': ''})
    assert len(json.dumps(first)) > 1_000_000 and first['etag']
    assert seen[-1] == {}  # _etag never reaches the plugin

    again = api.plugin_call('account', 'list_models', {'_etag': first['etag']})
    assert again == {'ok': True, 'unchanged': True, 'etag': first['etag']}
    assert len(json.dumps(again)) < 64 * 1024 and len(json.dumps(again)) < 100

    data['models'].append({'id': 'new'})
    changed = api.plugin_call('account', 'list_models', {'_etag': first['etag']})
    assert 'unchanged' not in changed and changed['etag'] != first['etag']

    plain = api.plugin_call('account', 'list_models', {})
    assert 'etag' not in plain  # callers that don't opt in get the reply as before


def test_team_overview_same_etag_gets_a_tiny_reply(monkeypatch) -> None:
    import json

    from backend.uefn_plugins import spaces

    overview = {'ok': True, 'current': 's', 'label': 'Team', 'plugins': [{'pluginId': f'p{i}', 'pad': 'x' * 1700} for i in range(100)]}
    monkeypatch.setattr(spaces, 'team_overview', lambda space='': overview)
    api = PanelApi.__new__(PanelApi)

    first = api.plugin_team_overview('')
    assert first['plugins'] == overview['plugins'] and first['etag']
    again = api.plugin_team_overview('', first['etag'])
    assert again == {'ok': True, 'unchanged': True, 'etag': first['etag']}
    assert len(json.dumps(again)) < 100

    overview['plugins'][0]['pad'] = 'changed'
    changed = api.plugin_team_overview('', first['etag'])
    assert 'unchanged' not in changed and changed['etag'] != first['etag']
