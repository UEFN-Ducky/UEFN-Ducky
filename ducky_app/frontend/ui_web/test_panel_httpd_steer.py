"""/__panel_rpc steer_take / steer_ack / steer_pending (panel_httpd._steer_rpc)."""

from __future__ import annotations

import pytest

from frontend.ui_web import steer
from frontend.ui_web.agent_turns.testing import patch_turns
from frontend.ui_web import panel_httpd as httpd


@pytest.fixture
def chat(monkeypatch):
    patch_turns(monkeypatch, "is_agent_running", lambda _cid: True)
    pushed: list[dict] = []
    steer.bind_run("c1", pushed.append)
    yield pushed
    steer.end_run("c1")


def test_take_leases_and_pushes_nothing(chat):
    assert httpd._steer_rpc("steer_pending", {"conv_id": "c1"}) == {"result": {"pending": False}}
    steer.post("c1", "s1", "also fix the tests")
    assert httpd._steer_rpc("steer_pending", {"conv_id": "c1"}) == {"result": {"pending": True}}
    lease = httpd._steer_rpc("steer_take", {"conv_id": "c1"})["result"]
    assert lease["lease_id"] and lease["texts"] == ["also fix the tests"] and lease["steer_ids"] == ["s1"]
    assert chat == []
    assert httpd._steer_rpc("steer_pending", {"conv_id": "c1"}) == {"result": {"pending": False}}


def test_ack_pushes_once(chat):
    steer.post("c1", "s1", "stop after this file")
    lease_id = httpd._steer_rpc("steer_take", {"conv_id": "c1"})["result"]["lease_id"]
    assert httpd._steer_rpc("steer_ack", {"conv_id": "c1", "lease_id": lease_id}) == {"ok": True, "result": {"ok": True}}
    httpd._steer_rpc("steer_ack", {"conv_id": "c1", "lease_id": lease_id})
    assert [e["type"] for e in chat] == ["user_steered"]
    assert chat[0]["steer_id"] == "s1"


class _FakeWriter:
    def __init__(self, order: list[str], pending: bool) -> None:
        self.order, self.pending = order, pending

    def flush(self, conv_id=None, timeout=30.0) -> bool:
        if self.pending:
            self.order.append("flush")
            self.pending = False
        return True


@pytest.fixture
def live_server(tmp_path, monkeypatch):
    import socket

    from frontend.ui_web.chats import saves as chat_saves

    (tmp_path / "index.html").write_text("<!doctype html>", encoding="utf-8")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setattr(httpd, "PANEL_UI_HTTP_PORT", port)
    monkeypatch.setattr(httpd, "_server", None)
    monkeypatch.setattr(httpd, "_root", None)
    order: list[str] = []
    monkeypatch.setattr(steer, "has_queued", lambda _cid: order.append("answer") or False)

    def use_writer(writer):
        monkeypatch.setattr(chat_saves, "_WRITER", writer)

    httpd.start_panel_ui_server(tmp_path)
    yield port, order, use_writer
    httpd._server.shutdown()
    httpd._server.server_close()


def _rpc(port: int, method: str, args: list) -> dict:
    import http.client
    import json

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("POST", "/__panel_rpc", body=json.dumps({"method": method, "args": args}))
        return json.loads(conn.getresponse().read())
    finally:
        conn.close()


def test_rpc_flushes_a_pending_save_once_before_answering(live_server):
    port, order, use_writer = live_server
    use_writer(_FakeWriter(order, pending=True))
    assert _rpc(port, "steer_pending", ["c1"]) == {"result": {"pending": False}}
    assert order == ["flush", "answer"]
    _rpc(port, "steer_pending", ["c1"])
    assert order == ["flush", "answer", "answer"]


def test_rpc_without_a_writer_does_not_flush(live_server):
    port, order, use_writer = live_server
    use_writer(None)
    _rpc(port, "steer_pending", ["c1"])
    assert order == ["answer"]


def test_positional_args_name_the_params():
    assert dict(zip(httpd._STEER_ARGS["steer_ack"], ["450a3ccd", "7f3c"])) == {"conv_id": "450a3ccd", "lease_id": "7f3c"}
