"""Append-only telemetry for Verse compile errors and verse-tool failures.

JSON lines at ``<app data>/verse_error_stats.jsonl`` (next to ``errors.jsonl``).
Every writer swallows I/O errors — telemetry must never break a tool.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path

STATS_FILENAME = "verse_error_stats.jsonl"
MAX_FILES_PER_ENTRY = 50


def stats_path() -> Path:
    """Same directory as the panel's ``errors.jsonl``; env fallback when frontend is absent."""
    try:
        from frontend.error_log import errors_path

        return errors_path().with_name(STATS_FILENAME)
    except Exception:  # noqa: BLE001 — standalone scripts have no frontend package
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "UEFN-Ducky" / STATS_FILENAME
        return Path.home() / ".uefn-ducky" / STATS_FILENAME


def _append(entry: dict) -> None:
    try:
        path = stats_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:  # noqa: BLE001
        pass


def record_compile(codes: dict[str, int], files: list[str]) -> None:
    """One compile that produced errors: ``codes`` = {"3512": 2, ...}, ``files`` = paths named."""
    _append(
        {
            "ts": time.time(),
            "kind": "compile",
            "codes": {str(k): int(v) for k, v in (codes or {}).items()},
            "files": [str(f) for f in (files or [])][:MAX_FILES_PER_ENTRY],
        }
    )


def record_tool_failure(tool: str, message: str) -> None:
    _append(
        {
            "ts": time.time(),
            "kind": "tool_failure",
            "tool": str(tool),
            "message": (message or "")[:500],
        }
    )


def _read_entries(since_ts: float) -> list[dict]:
    path = stats_path()
    if not path.is_file():
        return []
    out: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and float(entry.get("ts") or 0) >= since_ts:
                out.append(entry)
    except OSError:
        return []
    return out


def summarize(days: int = 30) -> dict:
    """Counts by error code, by failing tool, top files, and totals over the last ``days``."""
    since = time.time() - max(1, int(days)) * 86400.0
    entries = _read_entries(since)
    by_code: Counter = Counter()
    by_tool: Counter = Counter()
    by_file: Counter = Counter()
    compiles = 0
    tool_failures = 0
    for entry in entries:
        kind = entry.get("kind")
        if kind == "compile":
            compiles += 1
            for code, n in (entry.get("codes") or {}).items():
                try:
                    by_code[str(code)] += int(n)
                except (TypeError, ValueError):
                    continue
            for f in entry.get("files") or []:
                by_file[str(f)] += 1
        elif kind == "tool_failure":
            tool_failures += 1
            by_tool[str(entry.get("tool") or "?")] += 1
    return {
        "days": int(days),
        "path": str(stats_path()),
        "compiles_with_errors": compiles,
        "error_total": sum(by_code.values()),
        "tool_failures": tool_failures,
        "by_code": dict(by_code.most_common()),
        "by_tool": dict(by_tool.most_common()),
        "top_files": [[f, n] for f, n in by_file.most_common(10)],
    }
