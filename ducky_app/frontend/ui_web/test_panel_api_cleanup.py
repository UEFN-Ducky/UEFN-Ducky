"""plugin_call encodes a reply for its etag only when the panel asked for one."""

from __future__ import annotations

from backend.uefn_plugins.plugin_host.testing import patch_host
from frontend.ui_web.panel_api import PanelApi
from frontend.ui_web import panel_api_store  # after panel_api: the mixins import it

_DATA = {'ok': True, 'rows': ['x' * 100] * 10}


def _api(monkeypatch, data=_DATA) -> tuple[PanelApi, list[int]]:
    calls: list[int] = []
    real = panel_api_store._encode_reply

    def counting(reply):
        calls.append(1)
        return real(reply)

    monkeypatch.setattr(panel_api_store, '_encode_reply', counting)
    patch_host(monkeypatch, 'call_panel_rpc', lambda p, m, a: data)
    return PanelApi.__new__(PanelApi), calls


def test_a_call_without_etag_is_not_encoded_again(monkeypatch) -> None:
    api, calls = _api(monkeypatch)
    assert api.plugin_call('account', 'list_models', {}) == _DATA
    assert calls == []


def test_a_matching_etag_still_answers_unchanged(monkeypatch) -> None:
    api, _ = _api(monkeypatch)
    first = api.plugin_call('account', 'list_models', {'_etag': ''})
    assert first == {**_DATA, 'etag': first['etag']}
    again = api.plugin_call('account', 'list_models', {'_etag': first['etag']})
    assert again == {'ok': True, 'unchanged': True, 'etag': first['etag']}


def test_a_stale_etag_gets_the_full_reply_and_a_new_etag(monkeypatch) -> None:
    data = {'ok': True, 'rows': ['a']}
    api, _ = _api(monkeypatch, data)
    old = api.plugin_call('account', 'list_models', {'_etag': ''})['etag']
    data['rows'].append('b')
    out = api.plugin_call('account', 'list_models', {'_etag': old})
    assert out['rows'] == ['a', 'b'] and 'unchanged' not in out and out['etag'] != old
