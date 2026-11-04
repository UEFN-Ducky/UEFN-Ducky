"""Version tokens for panel replies: equal data gives an equal etag, so a caller holding the
current copy can be told "unchanged" instead of getting the whole reply again."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def encode_reply(reply: Any) -> str:
    """*reply* as compact, key-sorted JSON; "" when it can't be encoded."""
    try:
        return json.dumps(reply, ensure_ascii=False, default=str, separators=(",", ":"), sort_keys=True)
    except Exception:  # noqa: BLE001
        return ""


def reply_etag(encoded: str) -> str:
    """A short version token for one encoded reply (equal data, equal token)."""
    return hashlib.blake2b(encoded.encode("utf-8", "replace"), digest_size=12).hexdigest()
