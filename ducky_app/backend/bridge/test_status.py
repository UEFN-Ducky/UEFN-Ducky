"""Tests for GET-health-authoritative listener online / wedged status."""

from __future__ import annotations

from backend.bridge.status import ListenerStatusState, fetch_listener_status


def _patch_epic(monkeypatch, *, online: bool = False) -> None:
    monkeypatch.setattr(
        "backend.mcp_plugins.epic.probe_epic_mcp",
        lambda **_k: {
            "epic_mcp_online": online,
            "epic_mcp_reason": "" if online else "unreachable",
            "epic_mcp_url": "http://127.0.0.1:8000/mcp",
            "epic_mcp_setup_steps": [] if online else ["enable Auto Start"],
        },
    )


def test_busy_listener_stays_online(monkeypatch):
    _patch_epic(monkeypatch, online=True)
    monkeypatch.setattr(
        "backend.bridge.status.listener_get_health",
        lambda _port: {
            "status": "ok",
            "busy": True,
            "uptime_sec": 12,
            "tick_age_sec": 0.1,
            "current_command": "spawn_actor",
        },
    )

    def _fail_ping(*_a, **_k):
        raise AssertionError("status must never POST ping")

    monkeypatch.setattr("backend.bridge.status.ping_listener", _fail_ping)
    monkeypatch.setattr(
        "backend.bridge.status.post_command_to_listener",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no POST from status")),
    )

    status = fetch_listener_status(4200, state=ListenerStatusState(), version="test")
    assert status["online"] is True
    assert status["wedged"] is False
    assert status["busy"] is True
    assert "busy" in status["status_text"].lower()
    assert status["current_command"] == "spawn_actor"


def test_stale_tick_wedges_after_streak(monkeypatch):
    _patch_epic(monkeypatch)
    monkeypatch.setattr(
        "backend.bridge.status.listener_get_health",
        lambda _port: {"status": "ok", "busy": False, "uptime_sec": 30, "tick_age_sec": 25.0},
    )
    monkeypatch.setattr(
        "backend.bridge.status.ping_listener",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no POST ping")),
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
    _patch_epic(monkeypatch)
    monkeypatch.setattr("backend.bridge.status.listener_get_health", lambda _port: None)
    monkeypatch.setattr(
        "backend.bridge.status.ping_listener",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no ping when GET fails")),
    )

    status = fetch_listener_status(4200, state=ListenerStatusState(), version="test")
    assert status["online"] is False
    assert status["wedged"] is False
    assert status["status_text"].startswith("Offline")


def test_fresh_tick_is_online(monkeypatch):
    _patch_epic(monkeypatch, online=True)
    monkeypatch.setattr(
        "backend.bridge.status.listener_get_health",
        lambda _port: {
            "status": "ok",
            "busy": False,
            "uptime_sec": 90,
            "tick_age_sec": 0.05,
            "project_name": "MyIsland",
            "project_dir": "C:/proj",
        },
    )
    monkeypatch.setattr(
        "backend.bridge.status.ping_listener",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no POST ping")),
    )

    status = fetch_listener_status(4200, state=ListenerStatusState(), version="test")
    assert status["online"] is True
    assert status["wedged"] is False
    assert status["uptime_sec"] == 90.0
    assert status["uefn_project_name"] == "MyIsland"


def test_legacy_listener_without_tick_age_stays_online(monkeypatch):
    """Old listeners lack tick_age_sec — do not POST; stay online without wedging."""
    _patch_epic(monkeypatch, online=True)
    monkeypatch.setattr(
        "backend.bridge.status.listener_get_health",
        lambda _port: {"status": "ok", "busy": False, "uptime_sec": 45},
    )
    monkeypatch.setattr(
        "backend.bridge.status.ping_listener",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no POST ping")),
    )
    monkeypatch.setattr("backend.bridge.status.seconds_since_last_post_ok", lambda: 2.0)

    status = fetch_listener_status(4200, state=ListenerStatusState(), version="test")
    assert status["online"] is True
    assert status["wedged"] is False
    assert status["epic_mcp_online"] is True
    assert "Epic MCP online" in status["status_text"]


def test_epic_mcp_offline_includes_setup_steps(monkeypatch):
    _patch_epic(monkeypatch, online=False)
    monkeypatch.setattr("backend.bridge.status.listener_get_health", lambda _port: None)
    status = fetch_listener_status(4200, state=ListenerStatusState(), version="test")
    assert status["online"] is False
    assert status["epic_mcp_online"] is False
    assert status["epic_mcp_reason"] == "unreachable"
    assert status["epic_mcp_setup_steps"]
    assert "Epic MCP off" in status["status_text"]
