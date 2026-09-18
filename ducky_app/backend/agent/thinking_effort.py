"""Normalize advertised thinking-effort ids. Host invents Off=0 only."""

from __future__ import annotations

from typing import Any

_OFF_LEVEL: dict[str, Any] = {
    "id": "off",
    "label": "Off",
    "thinking_tokens": 0,
    "hint": "No extended thinking",
}


def normalize_thinking_effort(value: str | None) -> str:
    v = (value or "").strip().lower()
    if v in ("", "off", "none", "0"):
        return "off"
    return v


def _level_id(row: Any) -> str:
    if not isinstance(row, dict):
        return ""
    return str(row.get("id") or "").strip().lower()


def ensure_thinking_menu(menu: Any) -> dict[str, Any] | None:
    """Return a copy with Off first, or None when the gateway sent no levels."""
    if not isinstance(menu, dict):
        return None
    raw = menu.get("levels")
    if not isinstance(raw, list) or not raw:
        return None
    levels: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        lid = _level_id(item)
        if not lid or lid in seen or not isinstance(item, dict):
            continue
        seen.add(lid)
        row = dict(item)
        row["id"] = lid
        if lid == "off" and row.get("thinking_tokens") is None:
            row["thinking_tokens"] = 0
            row.setdefault("label", "Off")
            row.setdefault("hint", "No extended thinking")
        levels.append(row)
    if not levels:
        return None
    if "off" not in seen:
        levels.insert(0, dict(_OFF_LEVEL))
    out = dict(menu)
    out["levels"] = levels
    out.setdefault("lo", "Faster")
    out.setdefault("hi", "Smarter")
    return out
