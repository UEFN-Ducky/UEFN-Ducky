"""The listener refuses browsers and DNS rebinding: only Ducky's own client may run commands."""

from __future__ import annotations

import json
import socket
import sys
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_LISTENER_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def runtime(monkeypatch):
    unreal = types.ModuleType("unreal")
    unreal.log = unreal.log_warning = unreal.log_error = lambda *_a, **_k: None
    unreal.register_slate_post_tick_callback = lambda _fn: object()
    unreal.unregister_slate_post_tick_callback = lambda _h: None
    monkeypatch.setitem(sys.modules, "unreal", unreal)
    for name in [m for m in sys.modules if m == "listener" or m.startswith("listener.")]:
        monkeypatch.delitem(sys.modules, name)
    tick = types.ModuleType("listener.tick")
    tick.tick_handler = lambda _dt: None
    status_window = types.ModuleType("listener.status_window")
    status_window.MCPStatusWindow = object
    monkeypatch.setitem(sys.modules, "listener.tick", tick)
    monkeypatch.setitem(sys.modules, "listener.status_window", status_window)
    monkeypatch.setattr(sys, "path", [str(_LISTENER_ROOT), *sys.path])
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setenv("UEFN_DUCKY_LISTENER_PORT", str(port))

    import listener.runtime as rt

    rt.start_listener(show_status=False)
    yield port
    if unreal._mcp_server is not None:
        rt.stop_listener()


def _status(port: int, method: str, headers: dict[str, str]) -> int:
    body = json.dumps({"command": "execute_python", "params": {"code": "x=1"}}).encode() if method == "POST" else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}/", data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def test_request_trusted_rules(runtime):
    from listener.http import request_trusted

    ok = {"Host": "127.0.0.1:4200", "X-Ducky-Listener": "1"}
    assert request_trusted(ok, post=True)
    assert request_trusted({"Host": "localhost:4200"}, post=False)
    assert request_trusted({"Host": "[::1]:4200", "X-Ducky-Listener": "1"}, post=True)
    assert not request_trusted({"Host": "127.0.0.1:4200"}, post=True)  # no header
    assert not request_trusted({**ok, "Origin": "https://evil.example"}, post=True)
    assert not request_trusted({**ok, "Origin": "null"}, post=True)
    assert not request_trusted({**ok, "Host": "evil.example:4200"}, post=True)  # DNS rebinding
    assert not request_trusted({**ok, "Host": "127.0.0.1.evil.example"}, post=True)
    assert not request_trusted({"X-Ducky-Listener": "1"}, post=True)  # no Host


def test_server_refuses_untrusted_requests(runtime):
    port = runtime
    assert _status(port, "GET", {}) == 200
    assert _status(port, "GET", {"Origin": "https://evil.example"}) == 403
    assert _status(port, "GET", {"Host": "evil.example"}) == 403
    # A browser no-cors POST: text/plain, an Origin, no custom header.
    assert _status(port, "POST", {"Content-Type": "text/plain", "Origin": "https://evil.example"}) == 403
    assert _status(port, "POST", {"Content-Type": "application/json"}) == 403
