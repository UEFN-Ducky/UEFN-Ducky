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
