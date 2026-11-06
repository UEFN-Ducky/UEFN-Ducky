"""HTTPS requests to a fixed set of public hosts for the video and GitHub web tools.

Each hop is checked against the caller's host list and the resolved address must be
public (the same rule as web_lookup), so a redirect cannot reach this PC or the LAN.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlsplit

from backend.tools.core.web_lookup import _blocked_ip
from frontend.ssl_boot import https_context

_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 4
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class WebHttpError(RuntimeError):
    """A request that could not be made or answered. The message is safe to show."""


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes = b""
    location: str = ""


SendFn = Callable[[str, str, bytes | None, dict[str, str], int, float], Response]


def host_allowed(host: str, hosts: tuple[str, ...]) -> bool:
    h = (host or "").strip().rstrip(".").lower()
    return any(h == allowed or h.endswith("." + allowed) for allowed in hosts)


def _send(url: str, method: str, body: bytes | None, headers: dict[str, str], limit: int, timeout: float) -> Response:
    parts = urlsplit(url)
    host = (parts.hostname or "").strip().rstrip(".").lower()
    ip = ""
    for info in socket.getaddrinfo(host, 443, 0, socket.SOCK_STREAM):
        candidate = info[4][0]
        if not _blocked_ip(ipaddress.ip_address(candidate)):
            ip = candidate
            break
    if not ip:
        raise WebHttpError("blocked address")
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    raw = socket.create_connection((ip, 443), timeout=timeout)
    try:
        sock = https_context().wrap_socket(raw, server_hostname=host)
        conn = http.client.HTTPSConnection(host, 443, timeout=timeout)
        conn.sock = sock
        sent = {"Host": host, "User-Agent": _BROWSER_UA, "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "identity", **headers}
        conn.request(method, path, body=body, headers=sent)
        resp = conn.getresponse()
        got = {str(k).lower(): str(v) for k, v in resp.getheaders()}
        status = int(resp.status)
        if status in _REDIRECTS:
            resp.close()
            conn.close()
            return Response(status=status, headers=got, location=got.get("location", ""))
        data = resp.read(limit + 1)
        resp.close()
        conn.close()
        if len(data) > limit:
            raise WebHttpError("answer too large")
        return Response(status=status, headers=got, body=data)
    except Exception:
        try:
            raw.close()
        except Exception:
            pass
        raise


_sender: SendFn = _send


def request(
    url: str,
    *,
    hosts: tuple[str, ...],
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    limit: int = 2_000_000,
    timeout: float = 20.0,
) -> Response:
    """One HTTPS request, following up to four redirects inside ``hosts``. Raises WebHttpError."""
    current = url
    for _hop in range(_MAX_REDIRECTS + 1):
        parts = urlsplit(current)
        if parts.scheme != "https" or parts.username or parts.password:
            raise WebHttpError("only public https addresses")
        if parts.port not in (None, 443):
            raise WebHttpError("blocked port")
        if not host_allowed(parts.hostname or "", hosts):
            raise WebHttpError(f"{parts.hostname or 'that host'} is not one this tool reads")
        try:
            resp = _sender(current, method, body, dict(headers or {}), limit, timeout)
        except WebHttpError:
            raise
        except Exception as exc:
            raise WebHttpError(f"web request failed: {type(exc).__name__}") from exc
        if resp.status in _REDIRECTS:
            if not resp.location:
                raise WebHttpError("redirect missing location")
            current = urljoin(current, resp.location)
            if resp.status == 303 or (resp.status in (301, 302) and method == "POST"):
                method, body = "GET", None
            continue
        return resp
    raise WebHttpError("too many redirects")
