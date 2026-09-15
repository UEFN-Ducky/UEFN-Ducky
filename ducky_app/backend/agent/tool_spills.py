"""Spill oversized nested-tool results to AppData. Never keep the full string in chat."""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from frontend.app_paths import resolve_app_data_dir

INLINE_MAX = 12_000
PREVIEW_CHARS = 800
FILE_MAX_BYTES = 2 * 1024 * 1024
KEEP_FILES = 20
DIR_MAX_BYTES = 32 * 1024 * 1024
READ_MAX_CHARS = 8_000

_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.txt$")


def tool_spills_dir(*, for_write: bool = False) -> Path:
    path = resolve_app_data_dir(for_write=for_write) / "tool_spills"
    if for_write:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_tool(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", (name or "tool").strip())[:60] or "tool"


def _prune(directory: Path) -> None:
    files = [p for p in directory.iterdir() if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in files[KEEP_FILES:]:
        try:
            stale.unlink()
        except OSError:
            pass
    kept = [p for p in files[:KEEP_FILES] if p.exists()]
    kept.sort(key=lambda p: p.stat().st_mtime)
    total = 0
    sized: list[tuple[Path, int]] = []
    for p in kept:
        try:
            n = p.stat().st_size
        except OSError:
            continue
        sized.append((p, n))
        total += n
    i = 0
    while total > DIR_MAX_BYTES and i < len(sized):
        victim, n = sized[i]
        i += 1
        total -= n
        try:
            victim.unlink()
        except OSError:
            pass


def spill_tool_result(tool_name: str, text: str) -> str:
    """Write payload to tool_spills/, return a small JSON stub, drop the raw string."""
    original_chars = len(text)
    encoded = text.encode("utf-8")
    capped = len(encoded) > FILE_MAX_BYTES
    if capped:
        stored = encoded[:FILE_MAX_BYTES].decode("utf-8", errors="ignore")
    else:
        stored = text
    directory = tool_spills_dir(for_write=True)
    filename = f"{_safe_tool(tool_name)}_{int(time.time())}_{uuid.uuid4().hex[:8]}.txt"
    path = directory / filename
    path.write_text(stored, encoding="utf-8")
    _prune(directory)
    stub = {
        "truncated": True,
        "tool": tool_name,
        "chars": original_chars,
        "preview": text[:PREVIEW_CHARS],
        "path": str(path),
        "name": filename,
        "capped": capped,
        "hint": (
            "Full result spilled. Read with ducky_read_tool_spill(name, offset, max_chars) "
            "or re-call the tool with tighter args."
        ),
    }
    return json.dumps(stub, ensure_ascii=False)


def inline_or_spill(tool_name: str, text: str) -> str:
    if len(text) <= INLINE_MAX:
        return text
    return spill_tool_result(tool_name, text)


def resolve_spill_path(name: str) -> Path:
    raw = (name or "").strip().replace("\\", "/")
    if not raw or ".." in raw.split("/") or Path(name or "").is_absolute():
        raise ValueError("Invalid spill name")
    base = Path(raw).name
    if base != raw.rsplit("/", 1)[-1] or not _NAME_RE.match(base):
        raise ValueError("Invalid spill name")
    root = tool_spills_dir().resolve()
    target = (root / base).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("Path escapes tool_spills") from exc
    if not target.is_file():
        raise ValueError("Not a file")
    return target


def read_tool_spill(name: str, offset: int = 0, max_chars: int = 4000) -> dict[str, object]:
    path = resolve_spill_path(name)
    offset = max(0, int(offset))
    max_chars = max(1, min(int(max_chars), READ_MAX_CHARS))
    body = path.read_text(encoding="utf-8")
    chunk = body[offset : offset + max_chars]
    return {
        "ok": True,
        "name": path.name,
        "offset": offset,
        "max_chars": max_chars,
        "chars": len(body),
        "chunk": chunk,
        "truncated": offset + len(chunk) < len(body),
    }
