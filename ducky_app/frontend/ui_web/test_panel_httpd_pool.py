"""Panel HTTP server on fixed pools (panel_httpd._PanelServer + call_pools.http_pool)."""

from __future__ import annotations

import http.client
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from frontend.ui_web import call_pools
from frontend.ui_web import panel_httpd as httpd


class _Handler(httpd._PooledHandler):
    protocol_version = "HTTP/1.1"
    timeout = 30

    def log_message(self, *_args) -> None:
        return

    def _reply(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.startswith("/__panel_events"):
            time.sleep(3.0)  # a long-poll
        self._reply(threading.current_thread().name.encode())

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        time.sleep(0.01)
        self._reply(threading.current_thread().name.encode())


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(call_pools, "_pools", {})
    srv = httpd._PanelServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _post(port: int, path: str = "/__panel_api/load_messages") -> tuple[int, str]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        conn.request("POST", path, body=b"{}", headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, resp.read().decode()
    finally:
        conn.close()


def _http_threads() -> int:
    return sum(p.thread_count() for k, p in call_pools._pools.items() if k.startswith("http:"))


def test_pool_kind_routes():
    assert httpd._pool_kind(b"GET /__window_stream?v=1 HTTP/1.1\r\nHost: x") == "streams"
    assert httpd._pool_kind(b"GET /__panel_events?since=4 HTTP/1.1\r\n") == "long"
    assert httpd._pool_kind(b"POST /__panel_rpc HTTP/1.1\r\n") == "long"
    assert httpd._pool_kind(b"POST /__panel_api/pick_project_path HTTP/1.1\r\n") == "long"
    assert httpd._pool_kind(b"POST /__panel_api/load_messages HTTP/1.1\r\n") == "normal"
    assert httpd._pool_kind(b"POST /__panel_api/window_input HTTP/1.1\r\n") == "input"
    assert httpd._pool_kind(b"GET /assets/app.js HTTP/1.1\r\n") == "normal"
    assert httpd._pool_kind(b"") == "normal"


def test_300_concurrent_posts_bounded_threads(server):
    with ThreadPoolExecutor(max_workers=60) as ex:
        results = list(ex.map(lambda _i: _post(server), range(300)))
    assert all(status == 200 for status, _ in results)
    assert all(name.startswith("http-normal_") for _, name in results)
    # pools + the selector thread + the accept thread
    assert _http_threads() + 2 <= 70


def test_long_polls_do_not_delay_a_chat_load(server):
    socks = []
    for _ in range(40):
        s = socket.create_connection(("127.0.0.1", server))
        s.sendall(b"GET /__panel_events?since=0 HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        socks.append(s)
    try:
        time.sleep(0.3)
        _post(server)  # warm
        t0 = time.monotonic()
        status, name = _post(server)
        assert status == 200 and name.startswith("http-normal_")
        assert time.monotonic() - t0 < 0.1
    finally:
        for s in socks:
            s.close()


def test_silent_client_does_not_block_new_connections(server):
    silent = [socket.create_connection(("127.0.0.1", server)) for _ in range(30)]
    try:
        time.sleep(0.2)
        t0 = time.monotonic()
        status, _ = _post(server)
        assert status == 200
        assert time.monotonic() - t0 < 0.5
    finally:
        for s in silent:
            s.close()


def test_keep_alive_routes_each_request_again(server):
    conn = http.client.HTTPConnection("127.0.0.1", server, timeout=20)
    try:
        conn.request("POST", "/__panel_api/load_messages", body=b"{}")
        r1 = conn.getresponse()
        assert r1.read().decode().startswith("http-normal_")
        conn.request("GET", "/__panel_events?since=0")
        r2 = conn.getresponse()
        assert r2.read().decode().startswith("http-long_")
        conn.request("POST", "/__panel_api/load_messages", body=b"{}")
        r3 = conn.getresponse()
        assert r3.read().decode().startswith("http-normal_")
    finally:
        conn.close()
