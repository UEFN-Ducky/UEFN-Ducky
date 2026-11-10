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


def cert_failed(exc: BaseException) -> bool:
    return isinstance(getattr(exc, "reason", exc), ssl.SSLCertVerificationError)


def urlopen(req: urllib.request.Request, *, timeout: float, content_checked: bool = False) -> Any:
    """``urllib.request.urlopen``; ``content_checked`` downloads still arrive when this PC
    can't verify the server's certificate (a root Windows hasn't fetched, a wrong clock).

    Only for files the caller checks byte for byte against a hash it got from a verified,
    signed source (Store sha256, the update's signed manifest, the feed's installer
    sha256): a tampered file fails that check exactly as it would have over verified TLS.
    """
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as exc:
        if not (content_checked and cert_failed(exc)):
            raise
    from frontend.net_access import content_checked_opener

    return content_checked_opener().open(req, timeout=timeout)


# DNS-over-HTTPS resolvers addressed by IP (their certificates name the IP), so a PC whose
# own DNS can't resolve the Store host still finds it.
DOH_URLS = ("https://1.1.1.1/dns-query", "https://8.8.8.8/resolve")


def doh_resolve(host: str, *, timeout: float = 5.0) -> str:
    """First IPv4 address for ``host`` from a DoH JSON resolver; raises OSError when none answers."""
    import json
    from urllib.parse import quote

    last: BaseException = OSError(f"no DoH answer for {host}")
    for base in DOH_URLS:
        req = urllib.request.Request(f"{base}?name={quote(host)}&type=A",
                                     headers={"Accept": "application/dns-json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                answers = json.loads(resp.read().decode("utf-8", "replace")).get("Answer") or []
        except (OSError, ValueError) as exc:
            last = exc
            continue
        for row in answers:
            if isinstance(row, dict) and row.get("type") == 1 and row.get("data"):
                return str(row["data"])
    raise last if isinstance(last, OSError) else OSError(str(last))


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connects to ``ip`` but keeps ``host`` for SNI, certificate check and the Host header."""

    def __init__(self, host: str, *, ip: str, **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._ip = ip

    def connect(self) -> None:
        import socket

        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def pinned_opener(ip: str) -> urllib.request.OpenerDirector:
    class Handler(urllib.request.HTTPSHandler):
        def https_open(self, req: urllib.request.Request) -> Any:
            from frontend import ssl_boot

            return self.do_open(lambda host, **kw: _PinnedHTTPSConnection(host, ip=ip, **kw), req,
                                context=ssl_boot.https_context())

    return urllib.request.build_opener(Handler())


def doh_open(req: urllib.request.Request, *, timeout: float) -> Any:
    """Open ``req`` with its host looked up over DoH (PC DNS broken or filtered).
    The certificate is still verified against the real host name."""
    from urllib.parse import urlsplit

    return pinned_opener(doh_resolve(urlsplit(req.full_url).hostname or "")).open(req, timeout=timeout)


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
    content_checked: bool = False,
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
        resp = urlopen(req, timeout=timeout, content_checked=content_checked)
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
