"""The tray/taskbar icon heartbeat asks the UEFN listener only while UEFN runs, at most once a minute."""

from __future__ import annotations

import types

import pytest

from frontend.ui_web import webview_app as app


@pytest.fixture
def probes(monkeypatch):
    calls: list[int] = []
    health = {"value": {"status": "ok"}}

    def get_health(port, *a, **kw):
        calls.append(port)
        return health["value"]

    import backend.bridge as bridge

    monkeypatch.setattr(bridge, "listener_get_health", get_health)
    monkeypatch.setattr(app, "_probe", {"at": float("-inf"), "mode": "offline"})
    icons: list[str] = []
    monkeypatch.setattr(app, "_apply_shell_icons", lambda api, mode: icons.append(mode))
    modes: list[str] = []
    import frontend.usage_stats as usage_stats

    monkeypatch.setattr(usage_stats, "uefn_mode", lambda mode: modes.append(mode))
    return {"calls": calls, "health": health, "icons": icons, "modes": modes}


def _rounds(api, n: int) -> None:
    last = None
    for _ in range(n):
        last = app._poll_once(api, last)


def test_idle_with_uefn_closed_never_asks_the_listener(probes, monkeypatch):
    monkeypatch.setattr(app, "_uefn_process_running", lambda: False)
    _rounds(types.SimpleNamespace(), 10)
    assert probes["calls"] == []
    assert probes["icons"] == ["offline"] and probes["modes"] == ["offline"]


def test_a_cached_status_is_used_without_a_probe(probes, monkeypatch):
    monkeypatch.setattr(app, "_uefn_process_running", lambda: True)
    _rounds(types.SimpleNamespace(_last_listener_status={"online": True}), 10)
    assert probes["calls"] == []
    assert probes["icons"] == ["online"]


def test_with_uefn_running_the_listener_is_asked_at_most_once_a_minute(probes, monkeypatch):
    monkeypatch.setattr(app, "_uefn_process_running", lambda: True)
    clock = {"t": 1000.0}
    real = app._connection_mode_from_status
    monkeypatch.setattr(app, "_connection_mode_from_status", lambda cached: real(cached, now=clock["t"]))
    last = None
    for _ in range(30):  # 30 rounds, 8 s apart: four minutes
        last = app._poll_once(types.SimpleNamespace(), last)
        clock["t"] += 8.0
    assert len(probes["calls"]) == 4  # at 0, 64, 128 and 192 s
    assert probes["icons"] == ["online"]
    probes["health"]["value"] = None  # UEFN's listener went away: the next probe says so
    clock["t"] += 60.0
    app._poll_once(types.SimpleNamespace(), last)
    assert probes["icons"] == ["online", "offline"]
    assert probes["modes"] == ["online", "offline"]
