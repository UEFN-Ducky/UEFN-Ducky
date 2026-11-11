"""Persist hidden workflow cards independently of execution history."""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from frontend.atomic_json import write_json_atomic

_lock = threading.Lock()
# (folder, the folder's change time) -> the cards read then. The panel asks for the list every
# second while a workflow runs; reading one file per hidden card each time (355 files on one
# PC, and the folder only grows) took a quarter of the app's CPU (Nov 11 2026).
_read: tuple[str, int, list[dict[str, str]]] | None = None


def _directory() -> Path:
    from frontend.app_paths import resolve_app_data_dir

    return resolve_app_data_dir(for_write=True) / "workflow-card-dismissals"


def dismiss(chat_id: str, run_id: str) -> None:
    global _read
    if not chat_id or not run_id or len(chat_id) > 256 or len(run_id) > 256:
        raise ValueError("A chat ID and run ID are required")
    # One atomic record per pair: concurrent windows cannot overwrite other dismissals.
    key = hashlib.sha256(json.dumps([chat_id, run_id]).encode()).hexdigest()
    write_json_atomic(_directory() / (key + ".json"), {"chat_id": chat_id, "run_id": run_id})
    with _lock:
        _read = None


def dismissed() -> list[dict[str, str]]:
    """Every hidden card. Read again only when the folder changed (a file added by this
    process or another one moves the folder's change time)."""
    global _read
    directory = _directory()
    try:
        stamp = directory.stat().st_mtime_ns
    except OSError:
        return []
    where = str(directory)
    with _lock:
        if _read is not None and _read[0] == where and _read[1] == stamp:
            return [dict(row) for row in _read[2]]
    result = []
    for path in directory.glob("*.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(item, dict) and isinstance(item.get("chat_id"), str) and isinstance(item.get("run_id"), str):
                result.append({"chat_id": item["chat_id"], "run_id": item["run_id"]})
        except (OSError, ValueError):
            continue
    with _lock:
        _read = (where, stamp, result)
    return [dict(row) for row in result]
