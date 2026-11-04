"""Caps so one call can't flood UEFN: reply size, request body size, open requests.

One unfiltered census reply was 4.4 MB and pushed UEFN up by 0.7 GB, and with many
agents the retries pile onto the same editor. Here:

* a reply bigger than ``UEFN_DUCKY_MAX_REPLY_MB`` (default 2) is cut down to fit:
  the biggest list in the result keeps its first items, with ``truncated``, ``total``,
  ``returned`` and a hint to filter or page. Never the full blob.
* a request body over ``MAX_BODY_BYTES`` (4 MB) is refused before it is read
* at most ``MAX_OPEN_REQUESTS`` requests are open at once; the rest, like a request
  that finds the editor busy, get 503 with ``retry_after_ms`` so clients back off

Pure Python, no ``unreal`` import.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

DEFAULT_MAX_REPLY_MB = 2.0
MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_OPEN_REQUESTS = 16
TRUNCATE_HINT = "filter by class/folder/label or page with offset/limit"
# Room for the envelope and the truncation fields around the kept items.
_ENVELOPE_BYTES = 4096


def max_reply_bytes() -> int:
    try:
        mb = float(os.environ.get("UEFN_DUCKY_MAX_REPLY_MB") or DEFAULT_MAX_REPLY_MB)
    except ValueError:
        mb = DEFAULT_MAX_REPLY_MB
    return max(64 * 1024, int(mb * 1024 * 1024))


def body_too_large(content_length: int) -> bool:
    return int(content_length or 0) > MAX_BODY_BYTES


def retry_after_ms(in_flight_age_s: float = 0.0, queue_size: int = 0) -> int:
    """How long a refused client should wait: longer the longer the editor has been busy."""
    base = 250 + 250 * max(0, int(queue_size))
    return int(min(5000, max(base, float(in_flight_age_s) * 500.0)))


def _dump(value: Any) -> bytes:
    return json.dumps(value).encode()


def _biggest_list(result: Any) -> tuple[str | None, list | None]:
    """(key, list): the result itself when it is a list, else its biggest list value."""
    if isinstance(result, list):
        return None, result
    if isinstance(result, dict):
        lists = [(k, v) for k, v in result.items() if isinstance(v, list) and v]
        if lists:
            return max(lists, key=lambda kv: len(kv[1]))
    return None, None


def _fit(items: list, budget: int) -> int:
    """How many leading items fit in ``budget`` bytes (each item dumped once)."""
    used = 2
    for i, item in enumerate(items):
        used += len(_dump(item)) + 2
        if used > budget:
            return i
    return len(items)


def encode_reply(response: dict[str, Any], *, cap: int | None = None) -> bytes:
    """The response as JSON, cut down to ``cap`` bytes when it is bigger."""
    raw = _dump(response)
    cap = max_reply_bytes() if cap is None else cap
    if len(raw) <= cap:
        return raw
    result = response.get("result")
    key, items = _biggest_list(result)
    meta: dict[str, Any] = {"truncated": True, "hint": TRUNCATE_HINT, "reply_bytes": len(raw)}
    if items is not None:
        rest = len(raw) - len(_dump(items))  # everything but the list
        keep = _fit(items, cap - rest - _ENVELOPE_BYTES)
        meta.update(total=len(items), returned=keep)
        if key is None:
            capped: Any = {"items": items[:keep], **meta}
        else:
            capped = {**result, key: items[:keep], **meta}
    else:
        text = result if isinstance(result, str) else json.dumps(result)
        meta.update(total=len(text), returned=0)
        capped = {**meta, "preview": text[: max(0, min(len(text), cap // 4))]}
    out = _dump({**response, "result": capped})
    if len(out) > cap:  # a result whose other fields alone are too big
        out = _dump({**response, "result": {**meta, "returned": 0}})
    return out


class OpenRequests:
    """Counts open HTTP requests; ``enter`` is False when ``limit`` are already open."""

    def __init__(self, limit: int = MAX_OPEN_REQUESTS) -> None:
        self.limit = int(limit)
        self._lock = threading.Lock()
        self.open = 0

    def enter(self) -> bool:
        with self._lock:
            if self.open >= self.limit:
                return False
            self.open += 1
            return True

    def leave(self) -> None:
        with self._lock:
            self.open = max(0, self.open - 1)


OPEN_REQUESTS = OpenRequests()
