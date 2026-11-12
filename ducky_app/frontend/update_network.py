"""Bounded retries for temporary failures while checking/downloading updates."""
from __future__ import annotations

import errno
import http.client
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, BinaryIO, Callable, TypeVar

ATTEMPTS = 5
T = TypeVar("T")


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


# ---- the second storage host
#
# The site's download routes redirect to a link on its storage host. Some networks cut
# that host as the connection starts ("SSL: UNEXPECTED_EOF_WHILE_READING"); asked with
# ``?alt=1`` the same routes redirect to storage's second host, which those networks
# usually still reach. A site with one host answers ``?alt=1`` like any other request.

ALT_KEEP_S = 24 * 3600.0  # the host that worked is tried first this long
_ALT_PATHS = ("/api/v1/files/",)
_alt_until = 0.0


def alt_url(url: str) -> str:
    """``url`` asking for the second storage host, or ``""`` when it is not one of the
    site's download routes (a direct link, another site's) or already asks for it."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    query = parse_qsl(parts.query, keep_blank_values=True)
    if not parts.path.startswith(_ALT_PATHS) or any(k == "alt" for k, _ in query):
        return ""
    return urlunsplit(parts._replace(query=urlencode([*query, ("alt", "1")])))


def host_order(url: str) -> list[str]:
    """The links to try for ``url``, in order: the usual one and its second-host twin, the
    twin first for a day after it was the one that worked."""
    other = alt_url(url)
    if not other:
        return [url]
    return [other, url] if time.time() < _alt_until else [url, other]


def host_worked(url: str, tried: str) -> None:
    """``tried`` (one of :func:`host_order` of ``url``) finished a download."""
    global _alt_until
    _alt_until = time.time() + ALT_KEEP_S if tried != url else 0.0


def fail_stage(exc: BaseException) -> str:
    """Where a download failed: ``http`` (the server answered an error), ``read`` (cut part
    way), else ``connect`` (DNS, TCP or the TLS handshake)."""
    if isinstance(exc, urllib.error.HTTPError):
        return "http"
    return "read" if isinstance(exc, http.client.IncompleteRead) else "connect"


def note_download_fail(exc: BaseException, *, url: str, tried: str, what: str) -> None:
    """Count an update download that gave up on one host (anonymous: which host, where it
    failed, the pack or Setup). Never raises."""
    try:
        from frontend import usage_stats

        usage_stats.track("update.download_fail", host="alt" if tried != url else "main", stage=fail_stage(exc),
                          what=what)
    except Exception:  # noqa: BLE001 — counting never gets in the way of an update
        pass


def with_alt(url: str, run: Callable[[str], T], *, what: str = "pack") -> T:
    """``run(link)`` for the usual link; when its connection keeps failing, once more for
    the second storage host. ``run`` may be called twice with different links for the same
    file, so a download that keeps its bytes goes on from them."""
    order = host_order(url)
    try:
        out = run(order[0])
    except Exception as exc:
        if len(order) < 2 or not retryable(exc):
            raise
        note_download_fail(exc, url=url, tried=order[0], what=what)
        out = run(order[1])
        host_worked(url, order[1])
        return out
    if len(order) > 1:
        host_worked(url, order[0])
    return out
