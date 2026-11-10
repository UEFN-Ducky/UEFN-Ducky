"""Keep-alive urllib handlers for the DuckyOS site (moved out of duckyos_account)."""

from __future__ import annotations

import http.client
import io
import select
import socket
import threading
import time
import urllib.error
import urllib.request
import urllib.response
from typing import Any

from frontend.ssl_boot import https_context

# A parked connection idle longer than this is closed, not reused: a router may
# have dropped it without telling us, and a request on it would hang to timeout.
_KEEPALIVE_IDLE_S = 30.0
_KEEPALIVE_MAX_PARKED = 4


def _still_open(conn: http.client.HTTPConnection) -> bool:
    """An idle keep-alive socket has nothing to read; readable means the site closed it."""
    sock = conn.sock
    if sock is None:
        return False
    try:
        readable, _, _ = select.select([sock], [], [], 0)
    except (OSError, ValueError):
        return False
    return not readable


class _KeepAlive:
    """Reuse site connections instead of a new TCP + TLS handshake per request.

    urllib sends ``Connection: close`` and drops the socket after every call.
    Remote access asks the site's mailbox every few seconds all day, so that was up
    to 1,500 handshakes an hour for empty answers. Redirects, proxies and HTTP
    errors still go through urllib's own handlers.
    """

    def _keepalive_init(self) -> None:
        self._parked: dict[tuple[type, str], list[tuple[float, http.client.HTTPConnection]]] = {}
        self._parked_lock = threading.Lock()

    def _take(self, key: tuple[type, str]) -> http.client.HTTPConnection | None:
        now = time.monotonic()
        with self._parked_lock:
            parked = self._parked.get(key) or []
            while parked:
                since, conn = parked.pop()
                if now - since < _KEEPALIVE_IDLE_S and _still_open(conn):
                    return conn
                conn.close()
        return None

    def _park(self, key: tuple[type, str], conn: http.client.HTTPConnection) -> None:
        with self._parked_lock:
            parked = self._parked.setdefault(key, [])
            if len(parked) < _KEEPALIVE_MAX_PARKED:
                parked.append((time.monotonic(), conn))
                return
        conn.close()

    def _keepalive_open(self, http_class: type, req: urllib.request.Request, **conn_args: Any) -> Any:
        if req._tunnel_host or not req.host:  # proxy CONNECT: urllib's own path
            return self.do_open(http_class, req, **conn_args)  # type: ignore[attr-defined]
        key = (http_class, req.host)
        headers = dict(req.unredirected_hdrs)
        headers.update({k: v for k, v in req.headers.items() if k not in headers})
        headers = {name.title(): val for name, val in headers.items()}
        timeout = req.timeout if isinstance(req.timeout, (int, float)) else socket.getdefaulttimeout()
        for fresh in (False, True):
            conn = None if fresh else self._take(key)
            reused = conn is not None
            if conn is None:
                conn = http_class(req.host, timeout=req.timeout, **conn_args)
            else:
                conn.timeout = timeout
                if conn.sock is not None:
                    conn.sock.settimeout(timeout)
            try:
                conn.request(
                    req.get_method(), req.selector, req.data, headers,
                    encode_chunked=req.has_header("Transfer-encoding"),
                )
                resp = conn.getresponse()
                body = resp.read()
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as err:
                conn.close()
                if reused:
                    continue  # the site closed a parked connection: ask once on a new one
                raise urllib.error.URLError(err) from err
            except OSError as err:
                conn.close()
                raise urllib.error.URLError(err) from err
            except BaseException:
                conn.close()
                raise
            if resp.will_close:
                conn.close()
            else:
                self._park(key, conn)
            out = urllib.response.addinfourl(io.BytesIO(body), resp.headers, req.get_full_url(), resp.status)
            out.msg = resp.reason  # type: ignore[attr-defined]
            return out
        raise urllib.error.URLError("connection closed")


class KeepAliveHTTPHandler(_KeepAlive, urllib.request.HTTPHandler):
    def __init__(self) -> None:
        super().__init__()
        self._keepalive_init()

    def http_open(self, req: urllib.request.Request) -> Any:
        return self._keepalive_open(http.client.HTTPConnection, req)


class KeepAliveHTTPSHandler(_KeepAlive, urllib.request.HTTPSHandler):
    def __init__(self) -> None:
        super().__init__()
        self._keepalive_init()

    def https_open(self, req: urllib.request.Request) -> Any:
        return self._keepalive_open(http.client.HTTPSConnection, req, context=https_context())
