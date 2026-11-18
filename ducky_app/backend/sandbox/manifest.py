"""manifest.json: what came into a sandbox, what ran in it, and what was seen.

Kept small on purpose: the newest MAX_EVENTS entries, each field cut short, so a long
session never grows it without bound.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from frontend.atomic_json import write_json_plain

MAX_EVENTS = 300
_MAX_FIELD = 600
_lock = threading.Lock()


def _file(sandbox: Path) -> Path:
    return sandbox / "manifest.json"


def read(sandbox: Path) -> dict[str, Any]:
    try:
        data = json.loads(_file(sandbox).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"events": []}
    except (OSError, ValueError):
        return {"events": []}


def _short(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= _MAX_FIELD else value[:_MAX_FIELD] + "…"
    if isinstance(value, list):
        return [_short(v) for v in value[:40]]
    if isinstance(value, dict):
        return {str(k)[:60]: _short(v) for k, v in list(value.items())[:40]}
    return value


def record(sandbox: Path, kind: str, **fields: Any) -> None:
    """Append one event (download, clone, worktree, setup, run, launch, stop, evidence)."""
    with _lock:
        data = read(sandbox)
        events = [e for e in data.get("events") or [] if isinstance(e, dict)]
        events.append({"at": round(time.time(), 1), "kind": kind, **_short(fields)})
        data["events"] = events[-MAX_EVENTS:]
        try:
            write_json_plain(_file(sandbox), data, indent=1)
        except OSError:
            pass


def summary(sandbox: Path, last: int = 25) -> dict[str, Any]:
    events = [e for e in read(sandbox).get("events") or [] if isinstance(e, dict)]
    counts: dict[str, int] = {}
    for e in events:
        counts[str(e.get("kind"))] = counts.get(str(e.get("kind")), 0) + 1
    return {"counts": counts, "recent": events[-last:]}
