"""Listener POSTs fail fast when UEFN is closed — never sit on REQUEST_TIMEOUT."""

from __future__ import annotations

import time
import urllib.error

import pytest

import backend.bridge.client as bridge


def _refused(*_a, **_k):
    raise urllib.error.URLError(ConnectionRefusedError(10061, "refused"))


def test_post_command_fail_fast_when_uefn_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "_post_json_locked", _refused)

    t0 = time.perf_counter()
    with pytest.raises(ConnectionError, match="listener offline"):
        bridge.post_command_to_listener(4200, "ping", {})
    # ponytail: the regression is waiting out REQUEST_TIMEOUT (30 s); a 2 s wall clock flaked on busy PCs.
    assert time.perf_counter() - t0 < bridge.REQUEST_TIMEOUT / 3


def test_send_command_fail_fast_pinned_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "_pinned_port", 4200)
    monkeypatch.setattr(bridge, "_discovered_port", 4200)
    monkeypatch.setattr(bridge, "_post_json_locked", _refused)
    bridge._invalidate_cache()

    t0 = time.perf_counter()
    with pytest.raises(ConnectionError, match="listener offline"):
        bridge.send_command("spawn_actor", {})
    assert time.perf_counter() - t0 < bridge.REQUEST_TIMEOUT / 3


def test_send_command_posts_without_health_get_or_discovery_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bridge, "_pinned_port", None)
    monkeypatch.setattr(bridge, "_discovered_port", None)
    monkeypatch.setattr(bridge, "configured_listener_port", lambda: 4200)
    bridge._invalidate_cache()

    def _no_health(*_a, **_k):
        raise AssertionError("a command is one POST: no health GET first")

    def _no_discover():
        raise AssertionError("a command never scans ports")

    monkeypatch.setattr(bridge, "listener_get_health", _no_health)
    monkeypatch.setattr(bridge, "discover_port", _no_discover)
    posted: list[int] = []

    def _fake_post(port, command, params, timeout=30.0):
        posted.append(port)
        return {"success": True, "result": {"ok": True, "command": command}}

    monkeypatch.setattr(bridge, "_post_json_locked", _fake_post)

    out = bridge.send_command("spawn_actor", {})
    assert out.get("ok") is True
    assert posted == [4200]


def test_expected_offline_is_not_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    logged: list[str] = []
    monkeypatch.setattr(
        "frontend.error_log.record_error",
        lambda _src, msg: logged.append(msg),
    )
    bridge._record_bridge_error(
        "Listener not reachable for 'describe_commands' (GET health failed on port 4200)"
    )
    bridge._record_bridge_error("Listener not reachable for 'reload_listener' (discovery failed)")
    bridge._record_bridge_error("Command 'device_graph_snapshot' timed out after 2.0s")
    assert logged == ["Command 'device_graph_snapshot' timed out after 2.0s"]


def test_manifest_probe_busy_is_not_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    sources: list[str] = []
    monkeypatch.setattr("frontend.error_log.record_error", lambda src, _msg: sources.append(src))
    bridge._record_bridge_error("Command 'describe_commands' timed out waiting for listener idle", "describe_commands")
    bridge._record_bridge_error("Command 'describe_commands' timed out after 4.0s", "describe_commands")
    assert sources == []


def test_a_timeout_names_the_command(monkeypatch: pytest.MonkeyPatch) -> None:
    from frontend import usage_events

    sources: list[str] = []
    monkeypatch.setattr("frontend.error_log.record_error", lambda src, _msg: sources.append(src))
    bridge._record_bridge_error("Command 'save_current_level' timed out after 30.0s", "save_current_level")
    bridge._record_bridge_error("Command 'ping' timed out waiting for listener idle", "ping")  # busy = alive
    bridge._record_bridge_error("Command 'reload_listener' timed out waiting for listener idle", "reload_listener")
    assert sources == ["bridge_timeout.save_current_level", "bridge_busy.reload_listener"]
    # The anonymous code keeps it, and it still counts as a bridge error.
    assert usage_events.code_of(sources[0]) == "bridge_timeout.save_current_level"
    assert usage_events.where_of(sources[0]) == "bridge"


def test_a_lone_ping_timeout_is_not_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    sources: list[str] = []
    monkeypatch.setattr("frontend.error_log.record_error", lambda src, _msg: sources.append(src))
    monkeypatch.setattr(bridge, "_last_ping_timeout", 0.0)
    clock = iter([1000.0, 1500.0, 1560.0])
    monkeypatch.setattr(bridge.time, "monotonic", lambda: next(clock))
    bridge._record_bridge_error("Command 'ping' timed out after 2.0s", "ping")  # lone
    bridge._record_bridge_error("Command 'ping' timed out after 2.0s", "ping")  # 500 s later: lone again
    bridge._record_bridge_error("Command 'ping' timed out after 2.0s", "ping")  # 60 s later: in a row
    assert sources == ["bridge_timeout.ping"]
