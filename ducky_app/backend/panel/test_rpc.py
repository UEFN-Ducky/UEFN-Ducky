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


def test_question_gate_retries_until_answer_and_fails_closed(monkeypatch):
    import pytest

    responses = iter([{"blocked": True}, {"blocked": True}, {"blocked": False}])
    calls = []
    def gate(method, params, *, timeout):
        calls.append((method, params, timeout))
        return next(responses)
    monkeypatch.setattr(rpc_mod, "panel_rpc", gate)
    rpc_mod.wait_for_question_answers("chat")
    assert len(calls) == 3
    assert all(method == "question_gate" and params == {"conv_id": "chat"} for method, params, _ in calls)
    assert all(timeout == float("inf") for _, _, timeout in calls)
    monkeypatch.setattr(rpc_mod, "panel_rpc", lambda *args, **kwargs: {"error": "panel not open"})
    with pytest.raises(RuntimeError, match="remain blocked"):
        rpc_mod.wait_for_question_answers("chat")
