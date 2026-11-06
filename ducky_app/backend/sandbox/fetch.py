"""Download one public file into a sandbox, streamed to disk with a size cap and a SHA-256.

Every hop (redirects included) must be a public http(s) address on port 80/443, checked
with the same rules as web_fetch, and the connection goes to the address that was checked.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import os
import re
import socket
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

from backend.tools.core.web_lookup import _blocked_ip, reject_url
from frontend.ssl_boot import https_context

MAX_BYTES = 800 * 1024 * 1024
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_UA = "UEFN-Ducky-Sandbox"


class FetchError(RuntimeError):
    """A download that could not be made. The message is shown to the agent."""


def safe_name(url: str, fallback: str = "download") -> str:
    name = unquote(os.path.basename(urlsplit(url).path)) or fallback
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or fallback
    return name[:120]


def _open(url: str, timeout: float) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
    parts = urlsplit(url)
    host = (parts.hostname or "").strip().rstrip(".").lower()
    port = parts.port or (443 if parts.scheme == "https" else 80)
    ip = ""
    for info in socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM):
        if not _blocked_ip(ipaddress.ip_address(info[4][0])):
            ip = info[4][0]
            break
    if not ip:
        raise FetchError("blocked address")
    raw = socket.create_connection((ip, port), timeout=timeout)
    sock: Any = https_context().wrap_socket(raw, server_hostname=host) if parts.scheme == "https" else raw
    conn = (http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection)(host, port, timeout=timeout)
    conn.sock = sock
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    conn.request("GET", path, headers={"Host": host, "User-Agent": _UA, "Accept-Encoding": "identity"})
    return conn, conn.getresponse()


def download(url: str, dest: Path, *, max_bytes: int = MAX_BYTES, expect_sha256: str = "",
             timeout: float = 60.0) -> dict[str, Any]:
    """Stream ``url`` to ``dest``. Raises FetchError (nothing is left behind on failure)."""
    current = url
    for _hop in range(6):
        why = reject_url(current)
        if why:
            raise FetchError(f"{current}: {why}")
        try:
            conn, resp = _open(current, timeout)
        except FetchError:
            raise
        except Exception as exc:
            raise FetchError(f"download failed: {type(exc).__name__}: {exc}") from exc
        if resp.status in _REDIRECTS:
            location = resp.getheader("location") or ""
            resp.close()
            conn.close()
            if not location:
                raise FetchError("redirect without a location")
            current = urljoin(current, location)
            continue
        if resp.status >= 400:
            resp.close()
            conn.close()
            raise FetchError(f"http {resp.status}")
        length = int(resp.getheader("content-length") or 0)
        if length > max_bytes:
            resp.close()
            conn.close()
            raise FetchError(f"the file is {length // 1024**2} MB; the limit is {max_bytes // 1024**2} MB")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        digest = hashlib.sha256()
        got = 0
        try:
            with open(tmp, "wb") as out:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    got += len(chunk)
                    if got > max_bytes:
                        raise FetchError(f"the file is over the {max_bytes // 1024**2} MB limit")
                    digest.update(chunk)
                    out.write(chunk)
            sha = digest.hexdigest()
            if expect_sha256 and sha.lower() != expect_sha256.lower():
                raise FetchError(f"checksum mismatch (got {sha}, expected {expect_sha256})")
            os.replace(tmp, dest)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        finally:
            resp.close()
            conn.close()
        return {"path": str(dest), "bytes": got, "sha256": sha, "url": url, "final_url": current,
                "content_type": resp.getheader("content-type") or ""}
    raise FetchError("too many redirects")
