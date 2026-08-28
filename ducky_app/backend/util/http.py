"""Stdlib HTTP/JSON helpers for Store plugins (canvas conduit, not vendor APIs)."""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, TypeVar

T = TypeVar("T")

_IMAGE_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


class HttpError(Exception):
    def __init__(self, message: str, *, status: int | None = None, detail: Any = None):
        super().__init__(message)
        self.status = status
        self.detail = detail


def encode_image(path: str) -> str:
    """Local image path → data URI."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"image not found: {path}")
    mime = _IMAGE_MIME.get(p.suffix.lower(), "image/jpeg")
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


def resolve_image(image: str) -> str:
    """Pass through http(s)/data URIs; encode local paths."""
    s = (image or "").strip()
    if not s:
        return ""
    if s.startswith(("http://", "https://", "data:")):
        return s
    return encode_image(s)


def http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: float = 60,
) -> Any:
    """One JSON HTTP call. Empty body → {}. HTTP/network/parse failures → HttpError."""
    data = None
    hdrs = dict(headers or {})
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
        hdrs.setdefault("Accept", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail_body = ""
        try:
            detail_body = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        detail: Any = detail_body
        try:
            detail = json.loads(detail_body) if detail_body else {}
        except (ValueError, TypeError):
            pass
        raise HttpError(
            f"HTTP {exc.code}",
            status=exc.code,
            detail=detail,
        ) from exc
    except urllib.error.URLError as exc:
        raise HttpError(f"network error: {exc.reason}") from exc
    if not body.strip():
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise HttpError(f"non-JSON response: {body[:200]}") from exc


def poll(
    get_status: Callable[[], T],
    *,
    done: Callable[[T], bool],
    failed: Callable[[T], bool],
    interval: float = 5,
    max_attempts: int = 120,
) -> T:
    """Call get_status until done/failed. Raises TimeoutError if attempts exhaust."""
    attempts = max(1, int(max_attempts))
    wait = max(1, float(interval))
    for _ in range(attempts):
        result = get_status()
        if done(result) or failed(result):
            return result
        time.sleep(wait)
    raise TimeoutError(f"did not finish within {int(attempts * wait)}s")
