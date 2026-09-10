"""Persist left/right workspace dock layout across sessions (AppData).

WebView localStorage alone is not enough — a fresh WebView2 profile or
storage wipe resets sidebars. This file is the durable source of truth.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from frontend.atomic_json import write_json_atomic
from frontend.settings import default_app_data_dir

def _path() -> Path:
    # Resolved per call (a module constant ignored a later LOCALAPPDATA change).
    return default_app_data_dir() / "workspace_dock.json"


def _use_db() -> bool:
    from backend.store.switch import use_db

    return use_db("workspace_state")


def _kv():
    from backend.store.importers import phase1
    from backend.store.repos import kv

    phase1.ensure("workspace_state")
    return kv


def load_all() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_window(window_id: str = "main") -> dict[str, Any] | None:
    key = (window_id or "main").strip() or "main"
    if _use_db():
        try:
            raw = _kv().get_doc("workspace_state", f"dock:{key}")
            return raw if isinstance(raw, dict) else None
        except (OSError, RuntimeError):
            pass
    raw = load_all().get(key)
    return raw if isinstance(raw, dict) else None


def save_window(window_id: str, snapshot: dict[str, Any]) -> None:
    if not isinstance(snapshot, dict):
        return
    key = (window_id or "main").strip() or "main"
    if _use_db():
        try:
            _kv().set_doc("workspace_state", f"dock:{key}", snapshot)
            return
        except (OSError, RuntimeError):
            pass
    data = load_all()
    data[key] = snapshot
    try:
        write_json_atomic(_path(), data)
    except OSError:
        pass
