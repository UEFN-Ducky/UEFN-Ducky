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


def _settings_api(monkeypatch, apply) -> tuple[PanelApi, list]:
    from frontend import autostart
    from frontend.settings import PanelSettings
    from frontend.ui_web import panel_api as _pa

    s = PanelSettings(start_with_windows=False, uefn_project_root="")
    saved: list = []
    monkeypatch.setattr(s.__class__, 'save', lambda self, *a, **k: saved.append(self.start_with_windows))
    monkeypatch.setattr(_pa.PanelSettings, 'load', classmethod(lambda cls, *a, **k: s))
    monkeypatch.setattr(_pa, 'apply_workspace_env', lambda *a, **k: None)
    monkeypatch.setattr(autostart, 'apply', apply)
    return PanelApi.__new__(PanelApi), saved


def test_a_failed_autostart_keeps_the_old_value_and_reports(monkeypatch) -> None:
    def refused(enabled):
        raise OSError('access denied')

    api, saved = _settings_api(monkeypatch, refused)
    out = api.save_agent_settings({'start_with_windows': True})
    assert out == {'ok': False, 'error': 'Could not change start with Windows'}
    assert saved == [False]
    api, saved = _settings_api(monkeypatch, lambda enabled: False)  # apply says the registry didn't match
    assert api.save_agent_settings({'start_with_windows': True})['ok'] is False and saved == [False]


def test_success_saves_as_before(monkeypatch) -> None:
    api, saved = _settings_api(monkeypatch, lambda enabled: True)
    assert api.save_agent_settings({'start_with_windows': True}) == 'Saved — keys encrypted in credentials.dat'
    assert saved == [True]


def test_a_stale_etag_gets_the_full_reply_and_a_new_etag(monkeypatch) -> None:
    data = {'ok': True, 'rows': ['a']}
    api, _ = _api(monkeypatch, data)
    old = api.plugin_call('account', 'list_models', {'_etag': ''})['etag']
    data['rows'].append('b')
    out = api.plugin_call('account', 'list_models', {'_etag': old})
    assert out['rows'] == ['a', 'b'] and 'unchanged' not in out and out['etag'] != old
