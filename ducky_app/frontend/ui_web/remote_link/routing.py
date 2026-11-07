"""Which pool serves a panel HTTP request, read from its first line without consuming it.

``normal`` / ``streams`` / ``long`` / ``input`` / ``chat``. Remote input (``input_lane``)
is picked on the selector thread itself, so it never waits for a normal worker
just to be routed.
"""

from __future__ import annotations

import socket
from typing import Any
from urllib.parse import unquote

from frontend.ui_web.remote_link.input_lane import is_input_method

PEEK_TIMEOUT_S = 2.0
# Endless and long-wait routes get their own pools so they never hold the normal one.
STREAM_ROUTES = frozenset({b"/__window_stream"})
LONG_ROUTES = frozenset({b"/__panel_events", b"/__panel_rpc", b"/__panel_run"})
# Opening a chat gets its own small pool: a burst of busy calls on the normal one never delays it.
CHAT_METHODS = frozenset({"load_messages", "load_older_messages"})
CHAT_POOL_THREADS = 4


def pool_kind(head: bytes) -> str:
    """``normal`` / ``streams`` / ``long`` / ``input`` / ``chat`` from a request's first line (peeked, not read)."""
    parts = head.split(b"\r\n", 1)[0].split(b" ")
    if len(parts) < 2:
        return "normal"
    method, target = parts[0], parts[1].split(b"?", 1)[0]
    if method == b"GET" and target in STREAM_ROUTES:
        return "streams"
    if target in LONG_ROUTES:
        return "long"
    if target.startswith(b"/__panel_api/"):
        from frontend.ui_web.call_pools import HTTP_POOL_SIZES, lane_for

        name = unquote(target[len(b"/__panel_api/"):].decode("latin-1")).strip("/")
        if method == b"POST" and is_input_method(name):
            return "input"
        if method == b"POST" and name in CHAT_METHODS:
            # ponytail: size registered here so call_pools needs no edit for this pool.
            HTTP_POOL_SIZES.setdefault("chat", CHAT_POOL_THREADS)
            return "chat"
        if lane_for(name) == "block":
            return "long"
    return "normal"


def peek_rfile(handler: Any) -> bytes:
    """The next request's first bytes from a keep-alive handler's rfile, without
    blocking: a pipelined request may already sit in its buffer, where neither the
    selector nor a socket peek can see it. Non-blocking, never a timeout: a
    timed-out SocketIO refuses every later read."""
    rfile = getattr(handler, "rfile", None)
    conn = getattr(handler, "connection", None)
    if rfile is None or conn is None or not hasattr(rfile, "peek"):
        return b""
    old = conn.gettimeout()
    try:
        conn.setblocking(False)
        return bytes(rfile.peek(1024) or b"")[:1024]
    except Exception:
        return b""
    finally:
        try:
            conn.settimeout(old)
        except Exception:
            pass


def peek_head(request: Any, handler: Any, timeout: float | None = PEEK_TIMEOUT_S) -> bytes:
    """First bytes of the next request; ``timeout=None`` never blocks (selector thread)."""
    if handler is not None:
        # Keep-alive: the bytes may already sit in rfile's buffer.
        return peek_rfile(handler)
    old = request.gettimeout()
    if timeout is None:
        request.setblocking(False)
    else:
        request.settimeout(timeout)
    try:
        return request.recv(1024, socket.MSG_PEEK)
    finally:
        request.settimeout(old)


def quick_kind(request: Any, handler: Any) -> str:
    """The pool for a request whose bytes already arrived, never blocking; ``""`` if unknown yet."""
    try:
        head = peek_head(request, handler, None)
        return pool_kind(head) if head else ""
    except Exception:
        return ""
