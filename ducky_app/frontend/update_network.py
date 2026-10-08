"""Bounded retries for temporary failures while checking/downloading updates."""
from __future__ import annotations

import errno
import http.client
import ssl
import urllib.error

ATTEMPTS = 3


def retryable(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (408, 429, 500, 502, 503, 504)
    if isinstance(exc, urllib.error.URLError):
        return retryable(exc.reason) if isinstance(exc.reason, BaseException) else False
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
