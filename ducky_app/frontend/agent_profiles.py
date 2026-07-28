"""Bundled and user agent profiles (ducky templates)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from frontend.settings import PanelSettings

_BUNDLED_JSON = Path(__file__).resolve().parent / "bundled_agent_profiles.json"
BLANK_PROFILE_ID = "__blank__"
PROFILE_FIELDS = (
    "name",
    "ducky_style",
    "ducky_personality",
    "when_to_use",
    "disabled_packs",
    "enabled_subskills",
    "disabled_tool_ids",
    "favorite_models",
    "tts_voice",
    "tts_speed",
)


def _load_bundled_raw() -> list[dict[str, Any]]:
    if not _BUNDLED_JSON.is_file():
        return []
    try:
        data = json.loads(_BUNDLED_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    profiles = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(profiles, list):
        return []
    out: list[dict[str, Any]] = []
    for item in profiles:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id") or "").strip()
        if not pid:
            continue
        out.append(_normalize_profile({**item, "id": pid, "kind": "bundled"}, bundled=True))
    return out


def bundled_profile_ids() -> frozenset[str]:
    return frozenset(p["id"] for p in _load_bundled_raw())


def _dedup_preserve_order(values: list[str]) -> list[str]:
    """Order-preserving de-dup for id lists (packs, tools, models).

    A repeated id is never meaningful — duplicates only bloat stored profiles
    (e.g. disabled_packs accumulating "verse" 8x) and mislead the profile editor.
    Keep the first occurrence.
    """
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _normalize_profile(raw: dict[str, Any], *, bundled: bool = False) -> dict[str, Any]:
    pid = str(raw.get("id") or "").strip()
    # Prefer deny-lists. Legacy allowlists (enabled_packs / tool_ids) are dropped —
    # empty deny = everything available (lazy-loaded).
    disabled_packs = raw.get("disabled_packs")
    disabled_tools = raw.get("disabled_tool_ids")
    favorites = raw.get("favorite_models")
    enabled_subs_raw = raw.get("enabled_subskills")
    enabled_subskills: dict[str, list[str]] = {}
    if isinstance(enabled_subs_raw, dict):
        for pack_id, ids in enabled_subs_raw.items():
            key = str(pack_id or "").strip()
            if not key or not isinstance(ids, list):
                continue
            enabled_subskills[key] = _dedup_preserve_order([str(x) for x in ids if str(x).strip()])
    return {
        "id": pid,
        "name": str(raw.get("name") or pid or "Untitled").strip() or "Untitled",
        "ducky_style": str(raw.get("ducky_style") or "artist").strip() or "artist",
        "ducky_personality": str(raw.get("ducky_personality") or "").strip(),
        "when_to_use": str(raw.get("when_to_use") or "").strip(),
        "tts_voice": str(raw.get("tts_voice") or "").strip(),
        "tts_speed": float(raw.get("tts_speed") or 0.0),
        "disabled_packs": (
            _dedup_preserve_order([str(x) for x in disabled_packs])
            if isinstance(disabled_packs, list)
            else []
        ),
        "enabled_subskills": enabled_subskills,
        "disabled_tool_ids": (
            _dedup_preserve_order([str(x) for x in disabled_tools])
            if isinstance(disabled_tools, list)
            else []
        ),
        # Single optional model override ("backend:model_id"). Legacy 2nd/3rd
        # slots are dropped on load; empty means the global default applies.
        "favorite_models": _dedup_preserve_order([str(x).strip() for x in favorites if str(x).strip()])[:1]
        if isinstance(favorites, list)
        else [],
        "kind": "bundled" if bundled else str(raw.get("kind") or "custom"),
    }


def _patch_profile(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key in PROFILE_FIELDS:
        if key in patch and patch[key] is not None:
            merged[key] = patch[key]
    merged["id"] = base["id"]
    merged["kind"] = base.get("kind", "bundled")
    return _normalize_profile(merged, bundled=merged.get("kind") == "bundled")


def _hidden_bundled_ids(settings: PanelSettings) -> frozenset[str]:
    raw = settings.hidden_bundled_agent_profile_ids
    if raw is None:
        return bundled_profile_ids()
    if not isinstance(raw, list):
        return bundled_profile_ids()
    if len(raw) == 0:
        # Legacy installs (pre opt-out default) keep built-ins visible.
        return frozenset()
    return frozenset(str(x).strip() for x in raw if str(x).strip())


def _merge_agent_profiles(
    settings: PanelSettings,
    *,
    apply_hidden: bool,
) -> list[dict[str, Any]]:
    overrides = settings.agent_profile_overrides if isinstance(settings.agent_profile_overrides, dict) else {}
    custom = settings.agent_profiles if isinstance(settings.agent_profiles, list) else []
    hidden = _hidden_bundled_ids(settings) if apply_hidden else frozenset()
    merged: list[dict[str, Any]] = []
    for bundled in _load_bundled_raw():
        bid = bundled["id"]
        if bid in hidden:
            continue
        if bid in overrides and isinstance(overrides[bid], dict):
            merged.append(_patch_profile(bundled, overrides[bid]))
        else:
            merged.append(bundled)
    for item in custom:
        if isinstance(item, dict) and str(item.get("id") or "").strip():
            merged.append(_normalize_profile(item, bundled=False))
    return merged


def list_bundled_agent_profile_templates() -> list[dict[str, Any]]:
    return list(_load_bundled_raw())


def list_agent_profiles(settings: PanelSettings | None = None) -> list[dict[str, Any]]:
    """Profiles shown in the settings library (built-ins hidden by default)."""
    s = settings or PanelSettings.load()
    return _merge_agent_profiles(s, apply_hidden=True)


def list_agent_profiles_available(settings: PanelSettings | None = None) -> list[dict[str, Any]]:
    """All bundled + custom profiles for spawn, delegation, and template pickers."""
    s = settings or PanelSettings.load()
    return _merge_agent_profiles(s, apply_hidden=False)


def get_agent_profile(profile_id: str, settings: PanelSettings | None = None) -> dict[str, Any] | None:
    pid = str(profile_id or "").strip()
    if not pid or pid == BLANK_PROFILE_ID:
        return None
    for profile in list_agent_profiles_available(settings):
        if profile.get("id") == pid:
            return profile
    return None


def _dedupe_profile_name(base_name: str, settings: PanelSettings) -> str:
    """Avoid collisions with existing profile names ("X Copy", "X Copy 2", …)."""
    existing = {str(p.get("name") or "").strip().lower() for p in list_agent_profiles(settings)}
    candidate = base_name.strip() or "Untitled"
    if candidate.lower() not in existing:
        return candidate
    n = 2
    while f"{candidate} {n}".lower() in existing:
        n += 1
    return f"{candidate} {n}"


def duplicate_agent_profile(profile_id: str, settings: PanelSettings | None = None) -> dict[str, Any]:
    """Clone any bundled or custom profile (with overrides applied) into a new custom profile."""
    s = settings or PanelSettings.load()
    source = get_agent_profile(profile_id, s)
    if not source:
        raise ValueError(f"Profile not found: {profile_id}")
    raw = {key: source.get(key) for key in PROFILE_FIELDS}
    raw["name"] = _dedupe_profile_name(f"{source.get('name') or 'Untitled'} Copy", s)
    raw["id"] = str(uuid.uuid4())
    raw["kind"] = "custom"
    return save_agent_profile(raw)


def save_agent_profile(profile: dict[str, Any]) -> dict[str, Any]:
    s = PanelSettings.load()
    raw = _normalize_profile(profile, bundled=False)
    pid = str(raw.get("id") or "").strip()
    bundled_ids = bundled_profile_ids()
    if pid in bundled_ids:
        raise ValueError("Bundled profiles cannot be saved as custom — use save_agent_profile_override")
    profiles = [p for p in (s.agent_profiles or []) if isinstance(p, dict)]
    if not pid:
        pid = str(uuid.uuid4())
        raw["id"] = pid
    raw["kind"] = "custom"
    found = False
    next_profiles: list[dict[str, Any]] = []
    for item in profiles:
        if str(item.get("id") or "") == pid:
            next_profiles.append(raw)
            found = True
        else:
            next_profiles.append(_normalize_profile(item, bundled=False))
    if not found:
        next_profiles.append(raw)
    s.agent_profiles = next_profiles
    s.validate()
    s.save()
    return raw


def save_agent_profile_override(bundled_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    bid = str(bundled_id or "").strip()
    if bid not in bundled_profile_ids():
        raise ValueError(f"Not a bundled profile: {bundled_id}")
    bundled = next((p for p in _load_bundled_raw() if p["id"] == bid), None)
    if not bundled:
        raise ValueError(f"Bundled profile not found: {bundled_id}")
    s = PanelSettings.load()
    overrides = dict(s.agent_profile_overrides or {})
    existing = overrides.get(bid) if isinstance(overrides.get(bid), dict) else {}
    merged_patch = {**existing, **{k: v for k, v in patch.items() if k in PROFILE_FIELDS}}
    # De-dup list fields before storing so a bloated override (e.g. enabled_packs
    # with "verse" repeated) heals itself on the next save instead of persisting.
    for _key in ("enabled_packs", "tool_ids", "favorite_models"):
        if isinstance(merged_patch.get(_key), list):
            merged_patch[_key] = _dedup_preserve_order([str(x) for x in merged_patch[_key]])
    overrides[bid] = merged_patch
    hidden = list(s.hidden_bundled_agent_profile_ids or bundled_profile_ids())
    if bid in hidden:
        hidden = [x for x in hidden if x != bid]
    s.hidden_bundled_agent_profile_ids = hidden
    s.agent_profile_overrides = overrides
    s.validate()
    s.save()
    return _patch_profile(bundled, merged_patch)


def delete_agent_profile(profile_id: str) -> None:
    pid = str(profile_id or "").strip()
    if not pid:
        raise ValueError("profile_id is required")
    bundled_ids = bundled_profile_ids()
    s = PanelSettings.load()
    if pid in bundled_ids:
        hidden = list(s.hidden_bundled_agent_profile_ids or bundled_profile_ids())
        if pid not in hidden:
            hidden.append(pid)
        s.hidden_bundled_agent_profile_ids = hidden
        overrides = dict(s.agent_profile_overrides or {})
        overrides.pop(pid, None)
        s.agent_profile_overrides = overrides
        s.validate()
        s.save()
        return
    profiles = [p for p in (s.agent_profiles or []) if isinstance(p, dict) and str(p.get("id") or "") != pid]
    if len(profiles) == len(s.agent_profiles or []):
        raise ValueError(f"Profile not found: {profile_id}")
    s.agent_profiles = profiles
    s.validate()
    s.save()
