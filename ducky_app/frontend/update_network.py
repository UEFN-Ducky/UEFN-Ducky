"""Bounded retries for temporary failures while checking/downloading updates."""
from __future__ import annotations

import errno
import http.client
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, BinaryIO, Callable

ATTEMPTS = 5


def retryable(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (408, 429, 500, 502, 503, 504)
    if isinstance(exc, urllib.error.URLError):
        return retryable(exc.reason) if isinstance(exc.reason, BaseException) else False
    if isinstance(exc, (ssl.SSLEOFError, ssl.SSLZeroReturnError)):
        return True  # The connection dropped mid-TLS; nothing about the certificate failed.
    if isinstance(exc, ssl.SSLError):
        return False  # Certificate/security failures must never be bypassed.
    if isinstance(exc, OSError) and (
        exc.errno in (errno.ECONNRESET, errno.ECONNABORTED, errno.ETIMEDOUT,
                      10053, 10054, 10060)
        or getattr(exc, "winerror", None) in (10053, 10054, 10060)
    ):
        return True
    return isinstance(exc, (ConnectionError, TimeoutError,
                            http.client.IncompleteRead, http.client.RemoteDisconnected))


def retry_delay(attempt: int) -> float:
    return 0.5 * (attempt + 1)


def backoff(attempt: int) -> float:
    """Background downloads wait longer each time (1, 2, 4, 8, 16 s): a link that is down
    for half a minute (Wi-Fi switch, VPN reconnect, a busy storage edge) still finishes,
    where 5 tries in 5 s gave up."""
    return min(30.0, 2.0 ** attempt)


# A try that moved the file forward never counts against ATTEMPTS, so a link that drops
# every few hundred KB still finishes; this caps the total so it can't loop forever.
MAX_TRIES = 200


def fetch_into(
    url: str,
    out: BinaryIO,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 120.0,
    on_chunk: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    on_open: Callable[[Any], None] | None = None,
) -> None:
    """One GET that continues ``out`` from its current length with ``Range``.

    A server that ignores the range (200) starts the file over. Raises IncompleteRead
    when the body ends short, so the caller can try again from where it stopped.
    ``on_open`` gets the live response (then None) so a cancel can close it mid-read.
    """
    offset = out.tell()
    req_headers = dict(headers or {})
    if offset:
        req_headers["Range"] = f"bytes={offset}-"
    req = urllib.request.Request(url, headers=req_headers)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and offset:
            return  # nothing past what we have; callers check size and hash
        raise
    if on_open:
        on_open(resp)
    try:
        if offset and getattr(resp, "status", 200) != 206:
            out.seek(0)
            out.truncate()
            offset = 0
        try:
            length = int(resp.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        expected = offset + length if length else 0
        while True:
            if cancelled and cancelled():
                return
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            out.write(chunk)
            if on_chunk:
                on_chunk(out.tell(), expected)
        if expected and out.tell() != expected:
            raise http.client.IncompleteRead(b"", expected - out.tell())
    finally:
        if on_open:
            on_open(None)
        resp.close()


def download_resumable(url: str, out: BinaryIO, **kwargs: Any) -> None:
    """``fetch_into`` until the body is complete, resuming after every dropped connection.

    Gives up after ATTEMPTS tries in a row that got no new bytes, or a non-retryable error.
    ``delay`` (attempt -> seconds) is the wait between tries (default ``retry_delay``).
    """
    delay = kwargs.pop("delay", None) or retry_delay
    cancelled = kwargs.get("cancelled")
    misses = 0
    for _ in range(MAX_TRIES):
        before = out.tell()
        try:
            fetch_into(url, out, **kwargs)
            return
        except Exception as exc:
            if not retryable(exc):
                raise
            misses = 0 if out.tell() > before else misses + 1
            if misses >= ATTEMPTS or (cancelled and cancelled()):
                raise
            time.sleep(delay(misses))
    raise http.client.IncompleteRead(b"")
