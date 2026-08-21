"""Helpers for native Creative device ToyOptions (MCP census/edit is nested Epic)."""

from __future__ import annotations

from typing import Any


def _resolve_actor_path(actor_path: str = "", label: str = "") -> str:
    return (actor_path or label or "").strip()


def _filter_settings(result: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    """Keep only requested ToyOptions keys (case-sensitive first, then casefold)."""
    wanted = [k for k in keys if isinstance(k, str) and k.strip()]
    if not wanted:
        return result
    settings = result.get("settings")
    if not isinstance(settings, dict):
        result["keys_filtered"] = True
        return result
    by_fold = {str(k).casefold(): k for k in settings}
    out: dict[str, Any] = {}
    missing: list[str] = []
    for key in wanted:
        if key in settings:
            out[key] = settings[key]
        elif key.casefold() in by_fold:
            real = by_fold[key.casefold()]
            out[real] = settings[real]
        else:
            missing.append(key)
    result = {**result, "settings": out, "keys_filtered": True}
    if missing:
        result["keys_missing"] = missing
    return result
