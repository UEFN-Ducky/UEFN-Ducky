"""Remote input never waits behind chat or file traffic (remote_link.input_lane + panel_httpd)."""

from __future__ import annotations

import http.client
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from frontend.ui_web import call_pools
from frontend.ui_web import panel_httpd as httpd
from frontend.ui_web.remote_link import input_lane as il


class _Handler(httpd._PooledHandler):
    protocol_version = "HTTP/1.1"
    timeout = 30
    release = threading.Event()

    def log_message(self, *_args) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if "window_input" not in self.path:
            # An agent-time call: a chat load while UEFN is busy.
            _Handler.release.wait(5.0)
        body = threading.current_thread().name.encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(call_pools, "_pools", {})
    monkeypatch.setattr(il, "_lane", None)
    _Handler.release.clear()
    srv = httpd._PanelServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    _Handler.release.set()
    srv.shutdown()
    srv.server_close()


def _post(port: int, path: str) -> tuple[int, str, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        conn.request("POST", path, body=b"{}", headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, resp.read().decode(), dict(resp.getheaders())
    finally:
        conn.close()


def test_input_methods() -> None:
    assert il.is_input_method("window_input") and il.is_input_method("/direct_rtc_answer/")
    assert not il.is_input_method("load_messages")
    assert not il.is_input_method("direct_rtc_connect")  # waits up to 20 s for the page
    assert httpd._pool_kind(b"POST /__panel_api/window_input HTTP/1.1\r\n") == "input"
    assert httpd._pool_kind(b"GET /__panel_api/window_input HTTP/1.1\r\n") == "normal"


def test_input_under_100ms_while_300_agent_calls_fill_the_pools(server) -> None:
    ex = ThreadPoolExecutor(max_workers=300)
    try:
        busy = [ex.submit(_post, server, "/__panel_api/list_chats") for _ in range(300)]
        time.sleep(0.5)  # every normal worker held, the rest queued
        assert call_pools.http_pool("normal").thread_count() == call_pools.HTTP_POOL_SIZES["normal"]
        rtts = []
        for _ in range(20):
            t0 = time.monotonic()
            status, name, _ = _post(server, "/__panel_api/window_input")
            rtts.append(time.monotonic() - t0)
            assert status == 200 and name.startswith("remote-input_")
        rtts.sort()
        assert rtts[int(len(rtts) * 0.95) - 1] < 0.1, rtts
        _Handler.release.set()
        # Nothing was dropped: every queued agent call is answered, and says it waited.
        results = [f.result(timeout=30) for f in busy]
        assert all(status == 200 for status, _, _ in results)
        assert any("X-Ducky-Queued-Ms" in headers for _, _, headers in results)
    finally:
        _Handler.release.set()
        ex.shutdown(wait=True)
