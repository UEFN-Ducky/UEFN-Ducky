"""Full-transcript log for agent turns that crash or stall.

When a turn dies mid-stream (provider error, timeout, max-turns) we lose the
live reasoning from the screen. This keeps a durable record — the error, the
full reasoning/answer that had streamed so far, and timing (total elapsed +
first-token latency) — so a slow or crashing run can be diagnosed after the
fact. Stored as JSON lines at ``%LOCALAPPDATA%/UEFN-Ducky/agent_crashes.jsonl``,
auto-trimmed so it never grows without bound.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from frontend.settings import default_app_data_dir

MAX_ENTRIES = 500
MAX_AGE_DAYS = 14


def crashes_path() -> Path:
    return default_app_data_dir() / "agent_crashes.jsonl"


def record_crash(
    *,
    conv_id: str,
    provider: str,
    model: str,
    error: str,
    thinking: str,
    answer: str,
    elapsed_s: float,
    first_token_s: float | None,
) -> None:
    """Append one crash record with the full streamed transcript and timing."""
    path = crashes_path()
    entry: dict[str, Any] = {
        "ts": time.time(),
        "conv_id": conv_id,
        "provider": provider,
        "model": model,
        "error": (error or "").strip()[:2000],
        "elapsed_s": round(float(elapsed_s), 2),
        "first_token_s": (round(float(first_token_s), 2) if first_token_s is not None else None),
        "thinking_chars": len(thinking or ""),
        "answer_chars": len(answer or ""),
        "thinking": thinking or "",
        "answer": answer or "",
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        _maybe_trim(path)
    except OSError:
        pass


def read_crashes(limit: int = MAX_ENTRIES) -> list[dict[str, Any]]:
    """Return crash records newest-first."""
    path = crashes_path()
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
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


def _maybe_trim(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    kept: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ts = float(json.loads(line).get("ts") or 0)
        except ValueError:
            continue
        if ts >= cutoff:
            kept.append(line)
    if len(kept) > MAX_ENTRIES:
        kept = kept[-MAX_ENTRIES:]
    if len(kept) != len(lines):
        try:
            path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        except OSError:
            pass
