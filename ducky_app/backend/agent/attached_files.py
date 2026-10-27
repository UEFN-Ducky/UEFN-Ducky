"""Attached text files, as every model receives them.

A file that fits goes into the message whole. A bigger one is saved with a readable copy
next to it, and the message names both paths with the start of the file: the agent reads
the rest in parts with workspace_read_file. Same rule for coding agents and gateway models.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

# Base64 runs (data: URIs, bundled images and fonts) carry nothing a model can read.
_EMBEDDED_DATA = re.compile(r"(?:data:[\w.+/-]+;base64,)?[A-Za-z0-9+/]{400,}={0,2}")
# Readable copies wrap minified lines so start_line/end_line can page through them.
_WRAP_AT = 1000
# Share of the model's context window one message's files may fill when pasted whole.
INLINE_SHARE_OF_CONTEXT = 0.10
# ...and never more than this (~32 KiB of text): a bigger file is saved and read in parts, not
# re-sent whole on every later turn (10% of a 1M window was 100K tokens per message).
INLINE_MAX_TOKENS = 8_000
_HEAD_LINES = 120
_HEAD_CHARS = 8000
_HEADINGS = re.compile(r"^\s{0,3}(#{1,3})\s+(.+)$|<h([1-3])[^>]*>(.*?)</h\3>", re.IGNORECASE | re.MULTILINE)
_TAGS = re.compile(r"<[^>]+>")


def _size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    if n >= 1024:
        return f"{round(n / 1024)} KB"
    return f"{n} bytes"


def _slash(path: str) -> str:
    return (path or "").replace("\\", "/")


def strip_embedded_data(text: str) -> tuple[str, int]:
    """The text with base64 blocks replaced by a short note. Returns (text, characters left out)."""
    left_out = 0

    def drop(match: re.Match[str]) -> str:
        nonlocal left_out
        left_out += len(match.group(0))
        return f"[embedded data, {_size(len(match.group(0)))}, left out]"

    return _EMBEDDED_DATA.sub(drop, text or ""), left_out


def readable_text(text: str) -> str:
    """What the readable copy holds: no embedded data, long lines wrapped."""
    body, _ = strip_embedded_data(text)
    lines: list[str] = []
    for line in body.split("\n"):
        while len(line) > _WRAP_AT:
            lines.append(line[:_WRAP_AT])
            line = line[_WRAP_AT:]
        lines.append(line)
    return "\n".join(lines)


def readable_copy_needed(text: str) -> bool:
    raw = text or ""
    return bool(_EMBEDDED_DATA.search(raw)) or any(len(line) > _WRAP_AT for line in raw.split("\n"))


def readable_copy_path(full: Path) -> Path:
    return full.with_name(full.name + ".readable.txt")


def inline_token_budget(context_tokens: int) -> int:
    """Tokens of file text one message may paste whole; 0 = the window is unknown."""
    return min(int(max(0, context_tokens) * INLINE_SHARE_OF_CONTEXT), INLINE_MAX_TOKENS)


def _headings(text: str, limit: int = 40) -> list[str]:
    out: list[str] = []
    for m in _HEADINGS.finditer(text):
        level = len(m.group(1)) if m.group(1) else int(m.group(3))
        title = (m.group(2) or _TAGS.sub("", m.group(4) or "")).strip()
        if title:
            out.append(f"{'  ' * (level - 1)}- {title[:120]}")
        if len(out) >= limit:
            break
    return out


def file_block(
    name: str,
    text: str,
    *,
    count_tokens: Callable[[str], int],
    budget: int,
    saved_path: str = "",
    readable_path: str = "",
) -> tuple[str, str]:
    """(text for the message, "whole" or "parts").

    ``budget`` is what may be pasted whole (0 = unknown window: paste whole). Without a
    saved copy there is nothing to read in parts, so the file is pasted whole too.
    """
    raw = text or ""
    body, left_out = strip_embedded_data(raw)
    tokens = count_tokens(body)
    full = _slash(saved_path)
    where = _slash(readable_path) or full
    if not where or budget <= 0 or tokens <= budget:
        note = f"\n({_size(left_out)} of embedded data left out. Full file: {full})" if left_out and full else ""
        return f"Attached file: {name}{note}\n```\n{body}\n```", "whole"
    readable = readable_text(raw) if readable_path else body
    lines = readable.count("\n") + 1
    parts = [
        f"Attached file: {name} ({_size(len(raw.encode('utf-8')))}, about {tokens:,} tokens: "
        "too big to paste whole, so it is saved for you to read).",
    ]
    if full:
        parts.append(f"Full file: {full}")
    if readable_path:
        extra = f", {_size(left_out)} of embedded data left out" if left_out else ""
        parts.append(f"Readable copy: {where} ({lines:,} lines{extra})")
    parts.append(
        "Read it before you answer about it: workspace_read_file with path set to "
        f"{where} and start_line / end_line, a few hundred lines at a time."
    )
    heads = _headings(readable)
    if heads:
        parts.append("Headings:\n" + "\n".join(heads))
    head = "\n".join(readable.split("\n")[:_HEAD_LINES])[:_HEAD_CHARS]
    parts.append(f"Start of the file:\n```\n{head}\n```")
    return "\n".join(parts), "parts"
