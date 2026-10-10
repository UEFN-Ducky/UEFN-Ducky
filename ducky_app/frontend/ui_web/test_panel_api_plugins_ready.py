"""PanelApi construction must not push plugin-change events once plugins are loaded.

panel_httpd builds a PanelApi per HTTP request; a push per construction fed a
feedback loop with any remote viewer (event → refetch → new PanelApi → event).
"""

from __future__ import annotations


def test_ctor_does_not_rearm_ready_callback_when_loaded(monkeypatch) -> None:
    import backend.uefn_plugins.host as host
    from frontend.ui_web import panel_api

    calls: list[object] = []
    monkeypatch.setattr(host, "plugins_ready", lambda: True)
    monkeypatch.setattr(host, "ensure_plugins_loaded_async", lambda on_done=None: calls.append(on_done))
    api = panel_api.PanelApi.__new__(panel_api.PanelApi)
    api._start_plugins_load_async()
    api._start_plugins_load_async()
    assert calls == []


def test_ctor_arms_ready_callback_while_loading(monkeypatch) -> None:
    import backend.uefn_plugins.host as host
    from frontend.ui_web import panel_api

    calls: list[object] = []
    monkeypatch.setattr(host, "plugins_ready", lambda: False)
    monkeypatch.setattr(host, "ensure_plugins_loaded_async", lambda on_done=None: calls.append(on_done))
    api = panel_api.PanelApi.__new__(panel_api.PanelApi)
    api._start_plugins_load_async()
    assert len(calls) == 1 and callable(calls[0])


def test_ready_hook_queued_by_many_polls_fires_once(monkeypatch) -> None:
    # Oct 10 2026: every contributions / key-status / tool-list poll during boot queued
    # another ready hook; plugins-ready then pushed ~30 "plugins changed" events and the
    # panel refetched 500 KB of contributions for each.
    import types

    import backend.uefn_plugins.host as host
    from frontend.ui_web import panel_api, panel_httpd

    events: list[object] = []
    kicks: list[int] = []
    monkeypatch.setattr(host, "_LOADED", False)
    monkeypatch.setattr(host, "_LOAD_THREAD", types.SimpleNamespace(is_alive=lambda: True))
    monkeypatch.setattr(host, "_LOAD_CALLBACKS", [])
    monkeypatch.setattr(panel_httpd, "publish_panel_events", lambda evs: events.extend(evs))
    monkeypatch.setattr(panel_api, "kick_model_refresh", lambda: kicks.append(1))
    monkeypatch.setattr(panel_api, "_prune_model_caches_to_enabled_providers", lambda: None)

    api = panel_api.PanelApi.__new__(panel_api.PanelApi)
    for _ in range(10):
        host.ensure_plugins_loaded_async(on_done=api._notify_plugins_ready)
        host.ensure_plugins_loaded_async(on_done=panel_api.PanelApi.__new__(panel_api.PanelApi)._notify_plugins_ready)
    host._flush_load_callbacks()
    assert events == [{"type": "uefn_plugins_changed"}]
    assert kicks == [1]
