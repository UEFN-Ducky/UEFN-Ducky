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


def test_question_gate_with_stop_set_returns_within_one_round(monkeypatch):
    import threading

    stop = threading.Event()
    calls = []

    def gate(method, params, *, timeout):
        calls.append(method)
        stop.set()  # the call is given up on while this round runs
        return {"blocked": True}

    monkeypatch.setattr(rpc_mod, "panel_rpc", gate)
    assert rpc_mod.wait_for_question_answers("chat", stop) is False
    assert calls == ["question_gate"]
    assert rpc_mod.wait_for_question_answers("chat", stop) is False
    assert calls == ["question_gate"]  # already set: no round at all


def test_question_gate_sees_the_bound_stop_in_a_worker_thread(monkeypatch):
    import asyncio
    import threading

    stop = threading.Event()
    calls = []

    def gate(method, params, *, timeout):
        calls.append(method)
        if len(calls) == 2:
            stop.set()
        return {"blocked": True}

    monkeypatch.setattr(rpc_mod, "panel_rpc", gate)

    async def run():
        token = rpc_mod.bind_gate_stop(stop)
        try:
            return await asyncio.to_thread(rpc_mod.wait_for_question_answers, "chat")
        finally:
            rpc_mod.unbind_gate_stop(token)

    assert asyncio.run(run()) is False
    assert len(calls) == 2
    monkeypatch.setattr(rpc_mod, "panel_rpc", lambda *a, **k: {"blocked": False})
    assert rpc_mod.wait_for_question_answers("chat") is True  # unbound again: no stop
