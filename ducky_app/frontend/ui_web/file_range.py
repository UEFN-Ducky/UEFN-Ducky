"""Single-range HTTP ``Range`` handling for files the panel server streams (video seeking)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _parse_range_header(range_header: str | None, file_size: int) -> tuple[int, int] | None:
    """Parse a single-range ``Range: bytes=...`` header, clamped to ``file_size``.

    Returns an inclusive ``(start, end)`` byte range, or ``None`` if there is no
    (usable) Range header — callers should then serve the full body. Raises
    ``ValueError`` for a syntactically valid but unsatisfiable range (416).
    """
    if not range_header or file_size <= 0:
        return None
    if not range_header.startswith("bytes="):
        return None
    spec = range_header[len("bytes=") :].split(",", 1)[0].strip()
    if "-" not in spec:
        return None
    start_str, _, end_str = spec.partition("-")
    if start_str == "":
        # Suffix range: "bytes=-500" → last 500 bytes.
        if end_str == "":
            return None
        try:
            suffix_len = int(end_str)
        except ValueError:
            return None
        if suffix_len <= 0:
            raise ValueError("Unsatisfiable range")
        start = max(0, file_size - suffix_len)
        end = file_size - 1
    else:
        try:
            start = int(start_str)
        except ValueError:
            return None
        if end_str == "":
            end = file_size - 1
        else:
            try:
                end = int(end_str)
            except ValueError:
                return None
    if start < 0 or start >= file_size or end < start:
        raise ValueError("Unsatisfiable range")
    end = min(end, file_size - 1)
    return start, end


_MAX_RANGE_CHUNK = 4 * 1024 * 1024
_COPY_CHUNK = 1024 * 1024


def _plan_file_range(file_path: Path, range_header: str | None) -> tuple[int, dict[str, str], int, int]:
    """``(status, headers, start, length)`` for a file honoring a single ``Range``.

    Video seeking needs 206. Open-ended (``bytes=N-``) and oversized suffix ranges are
    capped at ``_MAX_RANGE_CHUNK`` so a browser's ``bytes=0-`` probe never loads the whole file.
    """
    size = file_path.stat().st_size
    try:
        byte_range = _parse_range_header(range_header, size)
    except ValueError:
        return 416, {"Content-Range": f"bytes */{size}"}, 0, 0
    if byte_range is None:
        return 200, {"Accept-Ranges": "bytes"}, 0, size
    start, end = byte_range
    spec = (range_header or "")[len("bytes=") :].split(",", 1)[0].strip()
    if (spec.endswith("-") or spec.startswith("-")) and end - start + 1 > _MAX_RANGE_CHUNK:
        end = start + _MAX_RANGE_CHUNK - 1
    headers = {"Accept-Ranges": "bytes", "Content-Range": f"bytes {start}-{end}/{size}"}
    return 206, headers, start, end - start + 1


def _copy_file_range(fh: Any, start: int, length: int, sink: Any) -> None:
    """Write ``length`` bytes of ``fh`` from ``start`` to ``sink`` in 1 MiB chunks."""
    fh.seek(start)
    remaining = length
    while remaining > 0:
        chunk = fh.read(min(_COPY_CHUNK, remaining))
        if not chunk:
            break
        sink.write(chunk)
        remaining -= len(chunk)
