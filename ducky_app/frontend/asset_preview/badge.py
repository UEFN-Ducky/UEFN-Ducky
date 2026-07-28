"""Generate small badge PNGs when the listener cannot export a real preview."""

from __future__ import annotations

import hashlib
import struct
import zlib


def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def make_badge_png(label: str, size: int = 64) -> bytes:
    """Solid-color 64×64 RGBA PNG with a hue derived from ``label``."""
    label = (label or "asset")[:32]
    digest = hashlib.sha256(label.encode()).digest()
    r = 50 + digest[0] % 100
    g = 70 + digest[1] % 100
    b = 100 + digest[2] % 100
    row = bytes([r, g, b, 255]) * size
    raw = b"".join([b"\x00" + row for _ in range(size)])
    compressed = zlib.compress(raw, 9)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", compressed)
        + _chunk(b"IEND", b"")
    )
