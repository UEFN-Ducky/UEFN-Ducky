"""Rolling 24-hour panel logs.

Stored as JSON lines under ``%LOCALAPPDATA%/UEFN-Ducky/``.
``errors.jsonl`` = failures; ``activity.jsonl`` = session/app lines.
Both are trimmed on every write and read so only the last 24 hours remain.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from frontend.settings import default_app_data_dir

MAX_ENTRIES = 2000
MAX_AGE_S = 24 * 3600
_ERRORS_NAME = "errors.jsonl"
_ACTIVITY_NAME = "activity.jsonl"


def errors_path() -> Path:
    return default_app_data_dir() / _ERRORS_NAME


def activity_path() -> Path:
    return default_app_data_dir() / _ACTIVITY_NAME


def record_error(source: str, message: str) -> None:
    """Append one error. Skips if identical to the most recent entry (dedupe spam)."""
    _append(_ERRORS_NAME, source, message, dedupe=True)


def record_activity(source: str, message: str) -> None:
    """Append one activity line (panel / plugin log)."""
    _append(_ACTIVITY_NAME, source, message, dedupe=False)


def read_errors(limit: int = MAX_ENTRIES) -> list[dict]:
    return _read(_ERRORS_NAME, limit)


def read_activity(limit: int = MAX_ENTRIES) -> list[dict]:
    return _read(_ACTIVITY_NAME, limit)


def clear_errors() -> None:
    _clear(_ERRORS_NAME)


def clear_activity() -> None:
    _clear(_ACTIVITY_NAME)


def trim() -> None:
    """Drop anything older than 24 hours. Safe to call anytime."""
    _trim_file(_ERRORS_NAME)
    _trim_file(_ACTIVITY_NAME)


def format_entries(entries: list[dict]) -> list[str]:
    """Newest-first lines for the Settings console."""
    lines: list[str] = []
    for e in entries:
        ts = e.get("ts", 0)
        try:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
        except (ValueError, TypeError):
            stamp = "?"
        lines.append(f"[{stamp}] ({e.get('source', '?')}) {e.get('message', '')}")
    return lines


def _path(name: str) -> Path:
    return default_app_data_dir() / name


def _append(name: str, source: str, message: str, *, dedupe: bool) -> None:
    message = (message or "").strip()
    if not message:
        return
    path = _path(name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if dedupe and _last_message(path) == message:
            return
        entry = {"ts": time.time(), "source": (source or "?")[:64], "message": message[:2000]}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        _trim_file(name)
    except OSError:
        pass


def _read(name: str, limit: int) -> list[dict]:
    _trim_file(name)
    path = _path(name)
    if not path.is_file():
        return []
    out: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
    except OSError:
        return []
    out.reverse()
    return out[: max(1, int(limit))]


def _clear(name: str) -> None:
    path = _path(name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
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


def _trim_file(name: str) -> None:
    path = _path(name)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    cutoff = time.time() - MAX_AGE_S
    kept: list[str] = []
    for ln in lines[-MAX_ENTRIES:]:
        try:
            if float(json.loads(ln).get("ts", 0)) >= cutoff:
                kept.append(ln)
        except (ValueError, TypeError):
            continue
    if kept == lines:
        return
    try:
        path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    except OSError:
        pass
