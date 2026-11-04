"""Request routing by first line (remote_link.routing)."""

from __future__ import annotations

import socket

from frontend.ui_web.remote_link import routing


def test_pool_kind_routes() -> None:
    assert routing.pool_kind(b"GET /__window_stream?v=1 HTTP/1.1\r\nHost: x") == "streams"
    assert routing.pool_kind(b"GET /__panel_events?since=4 HTTP/1.1\r\n") == "long"
    assert routing.pool_kind(b"POST /__panel_api/pick_project_path HTTP/1.1\r\n") == "long"
    assert routing.pool_kind(b"POST /__panel_api/window_input HTTP/1.1\r\n") == "input"
    assert routing.pool_kind(b"POST /__panel_api/load_messages HTTP/1.1\r\n") == "normal"
    assert routing.pool_kind(b"") == "normal"


def test_quick_kind_never_blocks_on_a_silent_socket() -> None:
    a, b = socket.socketpair()
    try:
        a.settimeout(30)
        assert routing.quick_kind(a, None) == ""  # nothing sent yet: returns at once
        assert a.gettimeout() == 30
        b.sendall(b"POST /__panel_api/window_input HTTP/1.1\r\n\r\n")
        for _ in range(100):
            if routing.quick_kind(a, None):
                break
        assert routing.quick_kind(a, None) == "input"
        # Peeked, not read: the handler still sees the whole request.
        assert a.recv(4) == b"POST"
    finally:
        a.close()
        b.close()
