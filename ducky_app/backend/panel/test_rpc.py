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


def test_rpc_follows_a_moved_port(monkeypatch):
    from frontend import settings

    urls: list[str] = []

    def fake_http(method: str, url: str, body, timeout: float):
        urls.append(url)
        return {"result": {"ok": True}}

    monkeypatch.setattr(rpc_mod, "_http", fake_http)
    monkeypatch.setattr(settings, "PANEL_LISTENER_PORT", 4211)
    rpc_mod.panel_rpc("ask_user", {})
    monkeypatch.setattr(settings, "PANEL_LISTENER_PORT", 4213)  # the pair moved 4210 -> 4212
    rpc_mod.panel_rpc("ask_user", {})
    assert urls == ["http://127.0.0.1:4210/__panel_rpc", "http://127.0.0.1:4212/__panel_rpc"]


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


_HOST_CALLS = [
    ("question_gate", {"conv_id": "rpc-direct"}),
    ("steer_take", {"conv_id": "rpc-direct"}),
    ("steer_ack", {"conv_id": "rpc-direct", "lease_id": ""}),
]


class _PipeConn:
    """A stand-in for the host's pipe: the request goes through steer_host as bytes."""

    def __init__(self) -> None:
        self._reply = b""

    def send_bytes(self, raw: bytes) -> None:
        import json

        from frontend.agent_host import steer_host

        request = json.loads(raw)
        self._reply = json.dumps(steer_host.answer(request["method"], request["params"])).encode("utf-8")

    def poll(self, timeout) -> bool:
        return True

    def recv_bytes(self) -> bytes:
        return self._reply

    def close(self) -> None:
        pass


def _fake_client(opened: list[str]):
    def client(pipe, authkey=None):
        opened.append(pipe)
        return _PipeConn()
    return client


def test_the_host_answers_its_own_request_without_a_pipe(monkeypatch):
    import multiprocessing.connection as mpc

    from frontend.agent_host import steer_host

    def no_pipe(*args, **kwargs):
        raise AssertionError("the host opened a pipe to itself")

    monkeypatch.setattr(mpc, "Client", no_pipe)
    monkeypatch.setattr(steer_host, "_address", (r"\\.\pipe\ducky-test-self", "00"))
    monkeypatch.setattr(steer_host, "GATE_WAIT_S", 0.01)
    for method, params in _HOST_CALLS:
        reply = rpc_mod.host_rpc(r"\\.\pipe\ducky-test-self", b"\0", method, params, 5.0)
        assert "error" not in reply, (method, reply)


def test_another_process_still_uses_the_pipe(monkeypatch):
    import multiprocessing.connection as mpc

    from frontend.agent_host import steer_host

    opened: list[str] = []
    monkeypatch.setattr(mpc, "Client", _fake_client(opened))
    monkeypatch.setattr(steer_host, "_address", None)  # this process serves no pipe
    rpc_mod.host_rpc(r"\\.\pipe\ducky-test-other", b"\0", "steer_ack", {"conv_id": "c", "lease_id": ""}, 5.0)
    assert opened == [r"\\.\pipe\ducky-test-other"]


def test_direct_and_pipe_replies_are_equal(monkeypatch):
    import multiprocessing.connection as mpc

    from frontend.agent_host import steer_host

    opened: list[str] = []
    monkeypatch.setattr(mpc, "Client", _fake_client(opened))
    monkeypatch.setattr(steer_host, "_address", (r"\\.\pipe\ducky-test-self", "00"))
    monkeypatch.setattr(steer_host, "GATE_WAIT_S", 0.01)
    for method, params in _HOST_CALLS:
        direct = rpc_mod.host_rpc(r"\\.\pipe\ducky-test-self", b"\0", method, params, 5.0)
        piped = rpc_mod.host_rpc(r"\\.\pipe\ducky-test-other", b"\0", method, params, 5.0)
        assert direct == piped, method
    assert len(opened) == len(_HOST_CALLS)
