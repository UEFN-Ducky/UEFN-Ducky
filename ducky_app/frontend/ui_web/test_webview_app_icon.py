"""The tray/taskbar icon heartbeat reads the last fetched listener status and never asks the listener."""

from __future__ import annotations

import types

import pytest

from frontend.ui_web import webview_app as app
from frontend.ui_web.lag import listener_status_cache


@pytest.fixture
def probes(monkeypatch):
    calls: list[int] = []

    def get_health(port, *a, **kw):
        calls.append(port)
        return {"status": "ok"}

    import backend.bridge as bridge

    monkeypatch.setattr(bridge, "listener_get_health", get_health)
    monkeypatch.setattr(listener_status_cache, "_last", None)
    icons: list[str] = []
    monkeypatch.setattr(app, "_apply_shell_icons", lambda api, mode: icons.append(mode))
    modes: list[str] = []
    import frontend.usage_stats as usage_stats

    monkeypatch.setattr(usage_stats, "uefn_mode", lambda mode: modes.append(mode))
    return {"calls": calls, "icons": icons, "modes": modes}


def _rounds(api, n: int) -> None:
    last = None
    for _ in range(n):
        last = app._poll_once(api, last)


def test_no_status_yet_is_offline_without_asking_the_listener(probes):
    _rounds(types.SimpleNamespace(), 40)  # over five minutes of 8 s rounds
    assert probes["calls"] == []
    assert probes["icons"] == ["offline"] and probes["modes"] == ["offline"]


def test_the_window_status_is_used_without_a_probe(probes):
    _rounds(types.SimpleNamespace(_last_listener_status={"online": True}), 10)
    assert probes["calls"] == []
    assert probes["icons"] == ["online"]


def test_the_newest_fetched_status_is_used_and_followed(probes, monkeypatch):
    monkeypatch.setattr(listener_status_cache, "_last", {"online": True})
    last = app._poll_once(types.SimpleNamespace(), None)
    monkeypatch.setattr(listener_status_cache, "_last", {"online": True, "wedged": True})
    last = app._poll_once(types.SimpleNamespace(), last)
    monkeypatch.setattr(listener_status_cache, "_last", {"online": False})
    app._poll_once(types.SimpleNamespace(), last)
    assert probes["calls"] == []
    assert probes["icons"] == ["online", "wedged", "offline"]
