"""In-memory editor state snapshots for MCP (path → state dict)."""

from __future__ import annotations

from typing import Any

_STATE: dict[str, dict[str, Any]] = {}


def _norm(path: str) -> str:
    return (path or "").strip().replace("\\", "/").lower()


def report_state(relative_path: str, state: dict[str, Any]) -> None:
    key = _norm(relative_path)
    if not key:
        return
    _STATE[key] = dict(state or {})


def get_state(relative_path: str) -> dict[str, Any] | None:
    key = _norm(relative_path)
    if not key:
        return None
    snap = _STATE.get(key)
    return dict(snap) if snap else None


def clear_state(relative_path: str | None = None) -> None:
    if relative_path is None:
        _STATE.clear()
        return
    _STATE.pop(_norm(relative_path), None)
