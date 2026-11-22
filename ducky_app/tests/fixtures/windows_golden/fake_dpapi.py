"""DPAPI with a throwaway key, for the golden files.

Real DPAPI seals for one Windows user on one PC, so a blob sealed here can't be opened by
a CI runner. The golden files are sealed with this stand-in instead: it replaces only the
one OS call (``secrets._dpapi``); every envelope around it (``UMCP`` secrets, ``enc1:``
rows, ``UDE1`` payloads, the account data key as entropy) is the product's own code.
Like DPAPI, the wrong entropy fails with ``OSError``. Never a real key: the key is the
constant below.
"""

from __future__ import annotations

import hashlib

MAGIC = b"GOLDENDPAPI1"
_KEY = b"windows-golden-throwaway-key"


def _stream(tag: bytes, n: int) -> bytes:
    out = bytearray()
    block = 0
    while len(out) < n:
        out += hashlib.sha256(_KEY + tag + block.to_bytes(4, "big")).digest()
        block += 1
    return bytes(out[:n])


def fake_dpapi(data: bytes, entropy: bytes | None, *, protect: bool) -> bytes:
    from backend.agent import secrets

    # The real call prepends the app's entropy the same way (secrets._dpapi).
    ent = secrets._ENTROPY + entropy if entropy is not None else b""
    tag = hashlib.sha256(_KEY + b"|" + ent).digest()[:16]
    if protect:
        return MAGIC + tag + bytes(a ^ b for a, b in zip(data, _stream(tag, len(data))))
    head = len(MAGIC) + len(tag)
    if data[: len(MAGIC)] != MAGIC or data[len(MAGIC):head] != tag:
        raise OSError("CryptUnprotectData failed")
    body = data[head:]
    return bytes(a ^ b for a, b in zip(body, _stream(tag, len(body))))


def install(setattr_) -> None:
    """``setattr_`` is ``monkeypatch.setattr`` in tests, ``setattr`` in the generator."""
    from backend.agent import secrets

    setattr_(secrets, "_dpapi", fake_dpapi)
    setattr_(secrets, "_dpapi_available", lambda: True)
