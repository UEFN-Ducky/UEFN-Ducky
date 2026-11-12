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


@pytest.mark.parametrize("reason", [TimeoutError("timed out"), ConnectionResetError(10054, "reset by peer")])
def test_a_lost_reply_is_not_called_offline(monkeypatch: pytest.MonkeyPatch, reason) -> None:
    """The POST reached UEFN and only the reply was lost: the job may still run there. Calling it
    'listener not running' made agents re-send a save job that was still going (UEFN ran it twice)."""
    monkeypatch.setattr(bridge, "_pinned_port", 4200)
    monkeypatch.setattr(bridge, "_discovered_port", 4200)
    monkeypatch.setattr(bridge, "configured_listener_port", lambda: 4200)

    def _lost(*_a, **_k):
        raise urllib.error.URLError(reason)

    monkeypatch.setattr(bridge, "_post_json_locked", _lost)
    bridge._invalidate_cache()
    with pytest.raises(TimeoutError, match="may still be running") as got:
        bridge.send_command("spawn_actor", {})
    assert "not running" not in str(got.value) and "Do not send it again" in str(got.value)


def test_a_reload_cut_off_by_the_listener_restarting_is_the_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nov 11 2026: an update's reload and a plugin update's met a second apart; the second got
    WinError 10054 from the restarting listener and was filed as bridge_timeout.reload_listener."""
    monkeypatch.setattr(bridge, "_pinned_port", 4200)
    monkeypatch.setattr(bridge, "_discovered_port", 4200)
    monkeypatch.setattr(bridge, "configured_listener_port", lambda: 4200)
    logged: list[str] = []
    monkeypatch.setattr(bridge, "_record_bridge_error", lambda msg, *a: logged.append(msg))
    state = {"reason": ConnectionResetError(10054, "An existing connection was forcibly closed by the remote host")}

    def _lost(*_a, **_k):
        raise urllib.error.URLError(state["reason"])

    monkeypatch.setattr(bridge, "_post_json_locked", _lost)
    bridge._invalidate_cache()
    out = bridge.send_command("reload_listener", timeout=6.0)
    assert out["ok"] is True and out["reloaded"] is True and logged == []

    # A reload that never answers is still an error, and so is any other command cut off.
    state["reason"] = TimeoutError("timed out")
    with pytest.raises(TimeoutError):
        bridge.send_command("reload_listener", timeout=6.0)
    state["reason"] = ConnectionResetError(10054, "reset")
    with pytest.raises(TimeoutError):
        bridge.send_command("spawn_actor", {})
    assert len(logged) == 2


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
