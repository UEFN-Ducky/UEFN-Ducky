"""Tests for GET-health-authoritative listener online / wedged status."""

from __future__ import annotations

from backend.bridge.status import ListenerStatusState, fetch_listener_status


def test_busy_listener_stays_online(monkeypatch):
    monkeypatch.setattr(
        "backend.bridge.status.listener_get_health",
        lambda _port: {"status": "ok", "busy": True, "uptime_sec": 12},
    )

    def _fail_ping(*_a, **_k):
        raise AssertionError("ping must not run while busy")

    monkeypatch.setattr("backend.bridge.status.ping_listener", _fail_ping)

    status = fetch_listener_status(4200, state=ListenerStatusState(), version="test")
    assert status["online"] is True
    assert status["wedged"] is False
    assert status["busy"] is True
    assert "busy" in status["status_text"].lower()


def test_ping_failure_keeps_online_and_wedges_after_streak(monkeypatch):
    monkeypatch.setattr(
        "backend.bridge.status.listener_get_health",
        lambda _port: {"status": "ok", "busy": False, "uptime_sec": 30},
    )
    monkeypatch.setattr(
        "backend.bridge.status.ping_listener",
        lambda *_a, **_k: (False, None),
    )
    # No recent successful POST — the wedge probe must actually ping.
    monkeypatch.setattr(
        "backend.bridge.status.seconds_since_last_post_ok",
        lambda: float("inf"),
    )
    monkeypatch.setattr(
        "backend.bridge.status.listener_project_fields",
        lambda *_a, **_k: ("", "", True, None),
    )
    state = ListenerStatusState()

    first = fetch_listener_status(4200, state=state, version="test")
    assert first["online"] is True
    assert first["wedged"] is False

    second = fetch_listener_status(4200, state=state, version="test")
    assert second["online"] is True
    assert second["wedged"] is True
    assert "wedged" in second["status_text"].lower()


def test_get_failure_is_offline(monkeypatch):
    monkeypatch.setattr("backend.bridge.status.listener_get_health", lambda _port: None)
    monkeypatch.setattr(
        "backend.bridge.status.ping_listener",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no ping when GET fails")),
    )

    status = fetch_listener_status(4200, state=ListenerStatusState(), version="test")
    assert status["online"] is False
    assert status["wedged"] is False
    assert status["status_text"].startswith("Offline")


def test_healthy_ping_is_online(monkeypatch):
    monkeypatch.setattr(
        "backend.bridge.status.listener_get_health",
        lambda _port: {"status": "ok", "busy": False},
    )
    monkeypatch.setattr(
        "backend.bridge.status.ping_listener",
        lambda *_a, **_k: (True, {"uptime_sec": 90}),
    )
    monkeypatch.setattr(
        "backend.bridge.status.seconds_since_last_post_ok",
        lambda: float("inf"),
    )
    monkeypatch.setattr(
        "backend.bridge.status.listener_project_fields",
        lambda *_a, **_k: ("", "", True, None),
    )

    status = fetch_listener_status(4200, state=ListenerStatusState(), version="test")
    assert status["online"] is True
    assert status["wedged"] is False
    assert status["uptime_sec"] == 90.0


def test_recent_agent_command_skips_status_ping(monkeypatch):
    monkeypatch.setattr(
        "backend.bridge.status.listener_get_health",
        lambda _port: {"status": "ok", "busy": False, "uptime_sec": 45},
    )

    def _fail_ping(*_a, **_k):
        raise AssertionError("ping must not run when an agent command just succeeded")

    monkeypatch.setattr("backend.bridge.status.ping_listener", _fail_ping)
    monkeypatch.setattr("backend.bridge.status.seconds_since_last_post_ok", lambda: 2.0)
    monkeypatch.setattr(
        "backend.bridge.status.listener_project_fields",
        lambda *_a, **_k: ("", "", True, None),
    )

    status = fetch_listener_status(4200, state=ListenerStatusState(), version="test")
    assert status["online"] is True
    assert status["wedged"] is False
