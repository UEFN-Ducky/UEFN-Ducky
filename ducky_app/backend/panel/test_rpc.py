"""panel_rpc: poll socket timeouts retry; connection refused is panel-not-open."""

from __future__ import annotations

from backend.panel import rpc as rpc_mod


def test_poll_socket_timeout_retries_then_succeeds(monkeypatch):
    calls: list[str] = []

    def fake_http(method: str, url: str, body, timeout: float):
        calls.append(method)
        if method == "POST":
            return {"pending": True, "request_id": "abc"}
        if len([c for c in calls if c == "GET"]) == 1:
            raise rpc_mod._Unreachable("timed out", timed_out=True)
        return {"result": {"ok": True}}

    monkeypatch.setattr(rpc_mod, "_http", fake_http)
    out = rpc_mod.panel_rpc("ask_user", {}, timeout=float("inf"))
    assert out.get("ok") is True
    assert calls.count("GET") >= 2


def test_connection_refused_is_panel_not_open(monkeypatch):
    def fake_http(method: str, url: str, body, timeout: float):
        raise rpc_mod._Unreachable("refused", timed_out=False)

    monkeypatch.setattr(rpc_mod, "_http", fake_http)
    out = rpc_mod.panel_rpc("ask_user", {}, timeout=float("inf"))
    assert out.get("error") == "panel not open"
