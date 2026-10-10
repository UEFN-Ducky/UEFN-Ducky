"""Listener POSTs fail fast when health is down — never sit on REQUEST_TIMEOUT."""

from __future__ import annotations

import threading
import time
import urllib.request

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


class _HeartbeatDone(BaseException):
    pass


class _TestThreadClock:
    """Fake time for the test thread; the real heartbeat thread keeps real time."""

    def __init__(self, stop_after: float) -> None:
        self.now = 0.0
        self.stop_after = stop_after
        self.owner = threading.current_thread()

    def time(self) -> float:
        return self.now if threading.current_thread() is self.owner else time.time()

    def sleep(self, seconds: float) -> None:
        if threading.current_thread() is not self.owner:
            time.sleep(seconds)
            return
        self.now += seconds
        if self.now > self.stop_after:
            raise _HeartbeatDone

    def __getattr__(self, name: str):
        return getattr(time, name)


def test_offline_heartbeat_backs_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """With UEFN closed every heartbeat was a full port scan (~70 connects), every
    13 s, in every Ducky process, all day."""
    test_thread = threading.current_thread()
    scans: list[float] = []
    clock = _TestThreadClock(stop_after=120.0)

    def offline(port: int, *, timeout: float = 1.0) -> bool:
        if threading.current_thread() is test_thread and port == bridge.DEFAULT_PORT:
            scans.append(clock.now)
        return False

    monkeypatch.setattr(bridge, "DEFAULT_PORT", bridge.MAX_PORT - 60)  # tests move it out of range
    monkeypatch.setattr(bridge, "_discovered_port", None)
    monkeypatch.setattr(bridge, "_pinned_port", None)
    monkeypatch.setattr(bridge, "_ping_port", offline)
    monkeypatch.setattr(bridge, "time", clock)
    with pytest.raises(_HeartbeatDone):
        bridge._heartbeat_loop()
    assert 1 <= len(scans) <= 4, f"{len(scans)} full scans in 2 minutes: {scans}"


def test_online_heartbeat_is_one_request(monkeypatch: pytest.MonkeyPatch) -> None:
    test_thread = threading.current_thread()
    pings: list[int] = []
    gets: list[str] = []
    real_urlopen = urllib.request.urlopen

    def online(port: int, *, timeout: float = 1.0) -> bool:
        if threading.current_thread() is test_thread:
            pings.append(port)
        return port == 4321

    def counting_urlopen(req, *args, **kwargs):
        if threading.current_thread() is test_thread:
            gets.append(getattr(req, "full_url", str(req)))
            raise OSError("no listener in tests")
        return real_urlopen(req, *args, **kwargs)

    monkeypatch.setattr(bridge, "_discovered_port", 4321)
    monkeypatch.setattr(bridge, "_pinned_port", None)
    monkeypatch.setattr(bridge, "_ping_port", online)
    monkeypatch.setattr(urllib.request, "urlopen", counting_urlopen)
    monkeypatch.setattr(bridge, "time", _TestThreadClock(stop_after=120.0))
    with pytest.raises(_HeartbeatDone):
        bridge._heartbeat_loop()
    assert pings == [4321] * 12
    assert gets == [], "a second GET per heartbeat"
