"""Listener traffic per client per minute, and the editor tick's cost (fake clock, fake requests)."""

from __future__ import annotations

import io
import json
import sys
import types
from pathlib import Path

import pytest

_LISTENER_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def listener(monkeypatch):
    unreal = types.ModuleType("unreal")
    unreal.log = unreal.log_warning = unreal.log_error = lambda *_a, **_k: None
    unreal._mcp_bound_port = 4200
    monkeypatch.setitem(sys.modules, "unreal", unreal)
    for name in [m for m in sys.modules if m == "listener" or m.startswith("listener.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "path", [str(_LISTENER_ROOT), *sys.path])
    import listener.traffic as traffic

    return traffic


class Clock:
    def __init__(self, now: float = 6000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_counts_per_client_per_minute_and_names_the_top_senders(listener) -> None:
    clock = Clock()
    t = listener.Traffic(clock=clock)
    for _ in range(14):
        t.record_request("adapter:3e1b", "GET", 200, 300)
    for _ in range(41):
        t.record_request("adapter:77aa", "POST", 503, 120)
    t.record_request("port:5123", "POST", 200, 50)
    row = t.table()["per_min"][0]
    assert (row["GET"], row["POST"], row["busy_503"]) == (14, 42, 41)
    assert row["bytes_out"] == 14 * 300 + 41 * 120 + 50
    assert row["top"][:2] == [["adapter:77aa", 41], ["adapter:3e1b", 14]]

    clock.now += 60  # next minute is its own row
    t.record_request("adapter:3e1b", "GET", 200, 1)
    assert [r["GET"] for r in t.table()["per_min"]] == [14, 1]
    s = t.summary()
    assert (s["GET"], s["POST"], s["busy_503"], s["clients"]) == (15, 42, 41, 3)


def test_keeps_only_the_last_ten_minutes(listener) -> None:
    clock = Clock()
    t = listener.Traffic(clock=clock)
    for minute in range(15):
        t.record_request("a", "GET", 200, 0)
        clock.now += 60
    clock.now -= 60
    table = t.table()
    assert len(table["per_min"]) == 10 and t.summary()["GET"] == 10


def test_tick_timing_and_frame_gap_hitch(listener) -> None:
    clock = Clock()
    t = listener.Traffic(clock=clock)
    at = clock.now
    for i in range(100):
        t.record_tick(at, 2.0 if i % 20 else 9.0)
        at += 0.016  # 60 fps
    at += 0.84  # a hitch: 840 ms with no tick
    t.record_tick(at, 2.0)
    row = t.table()["per_min"][0]
    assert row["ticks"] == 101 and row["tick_ms_max"] == 9.0
    assert 2.0 <= row["tick_ms_avg"] < 3.0
    assert row["tick_ms_p95"] in (2.0, 9.0)
    assert 850 <= t.summary()["frame_gap_ms_max"] <= 860


def test_client_key_from_header_else_peer_port(listener) -> None:
    assert listener.client_key({"X-Ducky-Client": "adapter:3e1b"}, ("127.0.0.1", 5000)) == "adapter:3e1b"
    assert listener.client_key({}, ("127.0.0.1", 5000)) == "port:5000"
    assert listener.client_key({}, None) == "unknown"


def test_listener_traffic_is_a_registered_command(listener) -> None:
    from listener.dispatch import dispatch, handler_names

    assert "listener_traffic" in handler_names()
    listener.TRAFFIC.record_request("a", "GET", 200, 10)
    out = dispatch("listener_traffic", {})
    assert out["window_min"] == 10 and out["per_min"][-1]["GET"] >= 1


def test_every_reply_is_counted_and_get_carries_the_summary(listener) -> None:
    import listener.http as http

    class Req(http.MCPRequestHandler):
        def __init__(self, method: str, headers: dict) -> None:  # no socket: a fake request
            self.command = method
            self.headers = headers
            self.client_address = ("127.0.0.1", 6001)
            self.wfile = io.BytesIO()
            self.request_version = "HTTP/1.1"
            self.requestline = f"{method} / HTTP/1.1"

        def send_response(self, code, message=None):
            self.code = code

        def send_header(self, *_a):
            pass

        def end_headers(self):
            pass

    listener.TRAFFIC = fresh = listener.Traffic()
    http.TRAFFIC = fresh
    get = Req("GET", {"Host": "127.0.0.1:4200", "X-Ducky-Client": "panel"})
    get.do_GET()
    body = json.loads(get.wfile.getvalue())
    assert body["traffic"]["window_min"] == 10
    assert fresh.summary()["GET"] == 1

    refused = Req("POST", {"Host": "evil.example"})
    refused.do_POST()
    row = fresh.table()["per_min"][-1]["clients"]
    assert row["panel"]["GET"] == 1 and row["port:6001"]["POST"] == 1
