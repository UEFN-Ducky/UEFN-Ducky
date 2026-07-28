"""Persistent error history for the control panel (so we keep more than the last error).

Stored as JSON lines at ``%LOCALAPPDATA%/UEFN-Ducky/errors.jsonl``. Auto-trimmed by count and age
so it never grows without bound.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from frontend.settings import default_app_data_dir

MAX_ENTRIES = 1000
MAX_AGE_DAYS = 14


def errors_path() -> Path:
    return default_app_data_dir() / "errors.jsonl"


def record_error(source: str, message: str) -> None:
    """Append one error. Skips if identical to the most recent entry (dedupe spam)."""
    message = (message or "").strip()
    if not message:
        return
    path = errors_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if _last_message(path) == message:
            return
        entry = {"ts": time.time(), "source": source, "message": message[:2000]}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        _maybe_trim(path)
    except OSError:
        pass


def read_errors(limit: int = MAX_ENTRIES) -> list[dict]:
    """Return entries newest-first (each: ts, source, message)."""
    path = errors_path()
    if not path.is_file():
        return []
    out: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        return []
    out.reverse()
    return out[:limit]


def clear_errors() -> None:
    path = errors_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    except OSError:
        pass


def trim() -> None:
    """Keep at most MAX_ENTRIES and drop entries older than MAX_AGE_DAYS. Safe to call anytime."""
    path = errors_path()
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    kept: list[str] = []
    for ln in lines[-MAX_ENTRIES:]:
        try:
            if float(json.loads(ln).get("ts", 0)) >= cutoff:
                kept.append(ln)
        except (ValueError, TypeError):
            continue
    if len(kept) == len(lines):
        return
    try:
        path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    except OSError:
        pass


def _last_message(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if not lines:
            return None
        return json.loads(lines[-1]).get("message")
    except (OSError, ValueError):
        return None


def _maybe_trim(path: Path) -> None:
    """Keep at most MAX_ENTRIES and drop entries older than MAX_AGE_DAYS."""
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return
    if len(lines) <= MAX_ENTRIES + 200:
        return  # amortize: only rewrite occasionally
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    kept: list[str] = []
    for ln in lines[-MAX_ENTRIES:]:
        try:
            if float(json.loads(ln).get("ts", 0)) >= cutoff:
                kept.append(ln)
        except (ValueError, TypeError):
            continue
    try:
        path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    except OSError:
        pass
