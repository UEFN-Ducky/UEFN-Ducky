"""One call can't flood UEFN: replies are capped, huge bodies refused, busy replies say when to retry."""

from __future__ import annotations

import io
import json
import sys
import types
from pathlib import Path

import pytest

_LISTENER_ROOT = Path(__file__).resolve().parents[1]
MB = 1024 * 1024


@pytest.fixture
def listener(monkeypatch):
    unreal = types.ModuleType("unreal")
    unreal.log = unreal.log_warning = unreal.log_error = lambda *_a, **_k: None
    unreal._mcp_bound_port = 4200
    unreal._mcp_in_flight = False
    unreal._mcp_in_flight_since = 0.0
    unreal._mcp_request_counter = 0
    monkeypatch.setitem(sys.modules, "unreal", unreal)
    for name in [m for m in sys.modules if m == "listener" or m.startswith("listener.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "path", [str(_LISTENER_ROOT), *sys.path])
    monkeypatch.delenv("UEFN_DUCKY_MAX_REPLY_MB", raising=False)
    import listener.limits as limits

    return types.SimpleNamespace(limits=limits, unreal=unreal)


def _actors(n: int) -> list[dict]:
    return [{"label": f"Actor_{i}", "class": "StaticMeshActor", "path": "/Game/" + "x" * 800} for i in range(n)]


def test_a_10mb_result_is_truncated_under_2mb_with_the_hint(listener) -> None:
    limits = listener.limits
    actors = _actors(12_040)
    response = {"success": True, "result": {"actors": actors, "count": len(actors)}}
    assert len(json.dumps(response)) > 9 * MB
    raw = limits.encode_reply(response)
    assert len(raw) <= 2 * MB
    out = json.loads(raw)["result"]
    assert out["truncated"] is True and out["total"] == 12_040
    assert 0 < out["returned"] == len(out["actors"]) < 12_040
    assert out["hint"] == limits.TRUNCATE_HINT and out["count"] == 12_040


def test_a_bare_list_and_a_huge_string_are_capped_too(listener) -> None:
    limits = listener.limits
    raw = limits.encode_reply({"success": True, "result": _actors(5000)}, cap=256 * 1024)
    out = json.loads(raw)["result"]
    assert len(raw) <= 256 * 1024 and out["truncated"] and len(out["items"]) == out["returned"]

    raw = limits.encode_reply({"success": True, "result": "y" * (3 * MB)}, cap=256 * 1024)
    out = json.loads(raw)["result"]
    assert len(raw) <= 256 * 1024 and out["truncated"] and out["total"] == 3 * MB


def test_a_small_reply_is_untouched_and_the_cap_follows_the_env(listener, monkeypatch) -> None:
    limits = listener.limits
    response = {"success": True, "result": {"actors": _actors(3)}}
    assert limits.encode_reply(response) == json.dumps(response).encode()
    monkeypatch.setenv("UEFN_DUCKY_MAX_REPLY_MB", "0.5")
    assert limits.max_reply_bytes() == MB // 2


def test_retry_after_grows_with_how_long_the_editor_is_busy(listener) -> None:
    limits = listener.limits
    assert limits.retry_after_ms(0.0) == 250
    assert limits.retry_after_ms(4.0) == 2000
    assert limits.retry_after_ms(600.0) == 5000


def test_open_requests_cap(listener) -> None:
    gate = listener.limits.OpenRequests(limit=2)
    assert gate.enter() and gate.enter() and not gate.enter()
    gate.leave()
    assert gate.enter()


class _Req:
    """A fake POST through MCPRequestHandler (no socket)."""

    def __init__(self, http, body: bytes, length: int | None = None) -> None:
        class Req(http.MCPRequestHandler):
            def __init__(self) -> None:
                self.command = "POST"
                self.headers = {"Host": "127.0.0.1:4200", "X-Ducky-Listener": "1",
                                "Content-Length": str(len(body) if length is None else length)}
                self.client_address = ("127.0.0.1", 7001)
                self.rfile = io.BytesIO(body)
                self.wfile = io.BytesIO()
                self.close_connection = False

            def send_response(self, code, message=None):
                self.code = code

            def send_header(self, *_a):
                pass

            def end_headers(self):
                pass

        self.req = Req()

    def run(self):
        self.req.do_POST()
        return self.req.code, json.loads(self.req.wfile.getvalue() or b"{}")


def test_an_oversized_body_is_refused_unread(listener) -> None:
    import listener.http as http

    code, body = _Req(http, b"{}", length=5 * MB).run()
    assert code == 413 and "limit" in body["error"]


def test_a_busy_reply_carries_retry_after_ms(listener) -> None:
    import time

    import listener.http as http

    listener.unreal._mcp_in_flight = True
    listener.unreal._mcp_in_flight_since = time.time() - 4.0
    code, body = _Req(http, json.dumps({"command": "get_all_actors", "params": {}}).encode()).run()
    assert code == 503 and 1500 <= body["retry_after_ms"] <= 2500


def test_too_many_open_requests_back_off(listener) -> None:
    import listener.http as http

    listener.limits.OPEN_REQUESTS.open = listener.limits.OPEN_REQUESTS.limit
    code, body = _Req(http, json.dumps({"command": "ping", "params": {}}).encode()).run()
    assert code == 503 and body["retry_after_ms"] >= 250
