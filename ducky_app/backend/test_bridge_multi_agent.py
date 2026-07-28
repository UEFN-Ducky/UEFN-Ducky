"""Bridge classification: 504→Timeout, busy-not-offline health retry."""

from __future__ import annotations

import io
import time
import urllib.error

import pytest

import backend.bridge as bridge


class _FakeHTTPError(urllib.error.HTTPError):
    def __init__(self, code: int, body: bytes = b"{}"):
        super().__init__(
            url="http://127.0.0.1:4200",
            code=code,
            msg="err",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(body),
        )


def test_504_raises_timeout_not_connection() -> None:
    err = _FakeHTTPError(
        504,
        b'{"success": false, "error": "Command timed out"}',
    )
    with pytest.raises(TimeoutError, match="editor busy") as ei:
        bridge._handle_listener_http_error(err, command="spawn_actor")
    assert "offline" not in str(ei.value).lower() or "do not assume" in str(ei.value).lower()


def test_503_raises_runtime_with_busy_hint() -> None:
    err = _FakeHTTPError(
        503,
        b'{"success": false, "error": "Listener queue full"}',
    )
    with pytest.raises(RuntimeError, match="queue full|busy") as ei:
        bridge._handle_listener_http_error(err, command="spawn_actor")
    assert "offline" not in str(ei.value).lower() or "do not assume" in str(ei.value).lower()


def test_probe_retries_when_recent_post_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[float] = []

    def _health(_port: int, timeout: float = 1.0):
        calls.append(timeout)
        if len(calls) == 1:
            return None
        return {"status": "ok", "busy": True}

    monkeypatch.setattr(bridge, "listener_get_health", _health)
    monkeypatch.setattr(bridge, "_last_post_ok_at", time.time())
    monkeypatch.setattr(bridge, "_command_depth", 0)
    monkeypatch.setattr(bridge.time, "sleep", lambda _s: None)

    out = bridge._probe_listener_health(4200)
    assert out is not None
    assert out.get("status") == "ok"
    assert calls[0] == bridge._HEALTH_FAIL_FAST_SEC
    assert calls[1] == bridge._HEALTH_RETRY_TIMEOUT_SEC


def test_probe_fail_fast_when_cold(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[float] = []

    def _health(_port: int, timeout: float = 1.0):
        calls.append(timeout)
        return None

    monkeypatch.setattr(bridge, "listener_get_health", _health)
    monkeypatch.setattr(bridge, "_last_post_ok_at", 0.0)
    monkeypatch.setattr(bridge, "_command_depth", 0)

    assert bridge._probe_listener_health(4200) is None
    assert calls == [bridge._HEALTH_FAIL_FAST_SEC]


def test_post_json_504_becomes_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "_wait_listener_idle", lambda *_a, **_k: None)

    def _urlopen(_req, timeout=30.0):
        raise _FakeHTTPError(504, b'{"success":false}')

    monkeypatch.setattr(bridge.urllib.request, "urlopen", _urlopen)
    with pytest.raises(TimeoutError, match="editor busy"):
        bridge._post_json_locked(4200, "spawn_actor", {}, timeout=2.0)
