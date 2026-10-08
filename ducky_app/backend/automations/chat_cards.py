"""Persist hidden workflow cards independently of execution history."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from frontend.atomic_json import write_json_atomic


def _directory() -> Path:
    from frontend.app_paths import resolve_app_data_dir

    return resolve_app_data_dir(for_write=True) / "workflow-card-dismissals"


def dismiss(chat_id: str, run_id: str) -> None:
    if not chat_id or not run_id or len(chat_id) > 256 or len(run_id) > 256:
        raise ValueError("A chat ID and run ID are required")
    # One atomic record per pair: concurrent windows cannot overwrite other dismissals.
    key = hashlib.sha256(json.dumps([chat_id, run_id]).encode()).hexdigest()
    write_json_atomic(_directory() / (key + ".json"), {"chat_id": chat_id, "run_id": run_id})


def dismissed() -> list[dict[str, str]]:
    result = []
    for path in _directory().glob("*.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(item, dict) and isinstance(item.get("chat_id"), str) and isinstance(item.get("run_id"), str):
                result.append({"chat_id": item["chat_id"], "run_id": item["run_id"]})
        except (OSError, ValueError):
            continue
    return result
