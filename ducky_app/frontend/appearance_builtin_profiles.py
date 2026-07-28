"""Built-in appearance profiles (Default only) — never persisted.

Light / Hacker shipped as Store plugins (``light``, ``hacker``).
"""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from frontend.bundle_root import is_packaged_runtime, packaged_data_root

DEFAULT_APPEARANCE_PROFILE_ID = "__default__"
# Legacy ids — removed from host; kept for migration of saved settings.
LIGHT_APPEARANCE_PROFILE_ID = "__light__"
HACKER_APPEARANCE_PROFILE_ID = "__hacker__"
_LEGACY_BUILT_IN_IDS = frozenset({LIGHT_APPEARANCE_PROFILE_ID, HACKER_APPEARANCE_PROFILE_ID})

_BUILTIN_JSON_NAME = "appearance_builtin_profiles.json"


def _resolve_builtin_json() -> Path | None:
    """Dev: ``frontend/appearance_builtin_profiles.json``. Frozen: ``<_MEIPASS>/frontend/...``."""
    if is_packaged_runtime():
        base = packaged_data_root()
        if base:
            candidate = base / "frontend" / _BUILTIN_JSON_NAME
            if candidate.is_file():
                return candidate
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidate = Path(meipass) / "frontend" / _BUILTIN_JSON_NAME
            if candidate.is_file():
                return candidate
    candidate = Path(__file__).resolve().parent / _BUILTIN_JSON_NAME
    return candidate if candidate.is_file() else None


def _default_only_fallback() -> list[dict[str, Any]]:
    return [
        {
            "id": DEFAULT_APPEARANCE_PROFILE_ID,
            "name": "Default",
            "foundation": {},
            "overrides": {},
            "status_overrides": {},
        }
    ]


@lru_cache(maxsize=1)
def _load_raw_profiles() -> list[dict[str, Any]]:
    path = _resolve_builtin_json()
    if path is None:
        return _default_only_fallback()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_only_fallback()
    profiles = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(profiles, list):
        return _default_only_fallback()
    out: list[dict[str, Any]] = []
    for item in profiles:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not pid or not name:
            continue
        foundation = item.get("foundation")
        overrides = item.get("overrides")
        status_overrides = item.get("status_overrides")
        out.append(
            {
                "id": pid,
                "name": name,
                "foundation": {str(k): str(v) for k, v in foundation.items() if v}
                if isinstance(foundation, dict)
                else {},
                "overrides": {str(k): str(v) for k, v in overrides.items() if v}
                if isinstance(overrides, dict)
                else {},
                "status_overrides": {
                    str(sid): {str(k): str(v) for k, v in fields.items() if v}
                    for sid, fields in status_overrides.items()
                    if isinstance(fields, dict)
                }
                if isinstance(status_overrides, dict)
                else {},
            }
        )
    return out or _default_only_fallback()


@lru_cache(maxsize=1)
def built_in_appearance_profile_ids() -> frozenset[str]:
    return frozenset(p["id"] for p in _load_raw_profiles())


def is_built_in_appearance_profile(profile_id: str | None) -> bool:
    return str(profile_id or "").strip() in built_in_appearance_profile_ids()


def get_built_in_appearance_profile(profile_id: str | None) -> dict[str, Any] | None:
    pid = str(profile_id or "").strip()
    if not pid:
        return None
    for profile in _load_raw_profiles():
        if profile["id"] == pid:
            return {
                "id": profile["id"],
                "name": profile["name"],
                "foundation": dict(profile["foundation"]),
                "overrides": dict(profile["overrides"]),
                "status_overrides": {
                    sid: dict(fields) for sid, fields in profile["status_overrides"].items()
                },
            }
    return None


def apply_built_in_appearance(s: Any, profile_id: str) -> bool:
    """Apply a built-in profile onto PanelSettings. Returns False if id unknown."""
    profile = get_built_in_appearance_profile(profile_id)
    if not profile:
        return False
    # Default uses empty foundation/overrides so the frontend merges CSS defaults.
    if profile["id"] == DEFAULT_APPEARANCE_PROFILE_ID:
        s.appearance_foundation = {}
        s.appearance_overrides = {}
        s.appearance_status_overrides = {}
    else:
        s.appearance_foundation = dict(profile["foundation"])
        s.appearance_overrides = dict(profile["overrides"])
        s.appearance_status_overrides = {
            sid: dict(fields) for sid, fields in profile["status_overrides"].items()
        }
    s.appearance_active_profile_id = profile["id"]
    return True
