"""Listener POSTs fail fast when health is down — never sit on REQUEST_TIMEOUT."""

from __future__ import annotations

import time

import pytest

import backend.bridge.client as bridge


def test_post_command_fail_fast_when_health_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "listener_get_health", lambda _port, timeout=1.0: None)
    posted: list[str] = []

    def _boom(*_a, **_k):
        posted.append("post")
        raise AssertionError("must not POST when health failed")

    monkeypatch.setattr(bridge, "_post_json_locked", _boom)

    t0 = time.perf_counter()
    with pytest.raises(ConnectionError, match="listener offline"):
        bridge.post_command_to_listener(4200, "ping", {})
    assert time.perf_counter() - t0 < 2.0
    assert posted == []


def test_send_command_fail_fast_pinned_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "_pinned_port", 4200)
    monkeypatch.setattr(bridge, "_discovered_port", 4200)
    monkeypatch.setattr(bridge, "listener_get_health", lambda _port, timeout=1.0: None)
    posted: list[str] = []

    def _boom(*_a, **_k):
        posted.append("post")
        return {"success": True, "result": {}}

    monkeypatch.setattr(bridge, "_post_json_locked", _boom)

    t0 = time.perf_counter()
    with pytest.raises(ConnectionError, match="listener offline"):
        bridge.send_command("ping", {})
    assert time.perf_counter() - t0 < 2.0
    assert posted == []


def test_send_command_uses_healthy_probe_without_discovery_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bridge, "_pinned_port", None)
    monkeypatch.setattr(bridge, "_discovered_port", None)
    monkeypatch.setattr(
        bridge, "listener_get_health", lambda _port, timeout=1.0: {"status": "ok"}
    )
    monkeypatch.setattr(bridge, "configured_listener_port", lambda: 4200)

    def _fake_post(_port, command, params, timeout=30.0):
        return {"success": True, "result": {"ok": True, "command": command}}

    monkeypatch.setattr(bridge, "_post_json_locked", _fake_post)
    discovered = []

    def _no_discover():
        discovered.append(1)
        raise AssertionError("healthy probe must skip discovery scan")

    monkeypatch.setattr(bridge, "discover_port", _no_discover)

    out = bridge.send_command("ping", {})
    assert out.get("ok") is True
    assert discovered == []
