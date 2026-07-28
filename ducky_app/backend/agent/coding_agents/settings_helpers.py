"""Helpers to read/write coding_agents settings on PanelSettings.

Defaults come from Store gateway registrations — no host vendor map.
"""

from __future__ import annotations

from typing import Any


def _registered_defaults() -> dict[str, dict[str, Any]]:
    """Merge contribution ids + optional settings_defaults from register()."""
    out: dict[str, dict[str, Any]] = {}
    try:
        from backend.uefn_plugins.host import (
            get_coding_agent_registration,
            get_contributions,
        )

        for row in get_contributions().get("llm_coding_agents") or []:
            if not isinstance(row, dict):
                continue
            aid = str(row.get("id") or "").strip().lower().replace("-", "_")
            if not aid:
                continue
            reg = get_coding_agent_registration(aid) or {}
            defaults = dict(reg.get("settings_defaults") or {})
            defaults.setdefault("enabled", True)
            defaults.setdefault("cli_path", "")
            defaults.setdefault("default_args", "")
            out[aid] = defaults
    except Exception:
        pass
    return out


def coding_agents_dict(settings: Any) -> dict[str, dict[str, Any]]:
    raw = getattr(settings, "coding_agents", None) or {}
    if not isinstance(raw, dict):
        raw = {}
    defaults_map = _registered_defaults()
    # Keep persisted rows for agents whose plugin is temporarily disabled.
    for key, entry in raw.items():
        aid = str(key or "").strip().lower().replace("-", "_")
        if aid and aid not in defaults_map and isinstance(entry, dict):
            defaults_map[aid] = {
                "enabled": True,
                "cli_path": "",
                "default_args": "",
            }
    out: dict[str, dict[str, Any]] = {}
    for key, defaults in defaults_map.items():
        entry = raw.get(key) if isinstance(raw.get(key), dict) else {}
        merged = dict(defaults)
        merged.update({k: v for k, v in entry.items() if v is not None})
        merged["enabled"] = bool(merged.get("enabled", True))
        merged["cli_path"] = str(merged.get("cli_path") or "")
        merged["default_args"] = str(
            merged.get("default_args") or defaults.get("default_args") or ""
        )
        out[key] = merged
    return out


def coding_agent_cfg(settings: Any, agent_id: str) -> dict[str, Any]:
    aid = (agent_id or "").strip().lower().replace("-", "_")
    return coding_agents_dict(settings).get(
        aid, {"enabled": True, "cli_path": "", "default_args": ""}
    )


def patch_coding_agents(existing: dict[str, Any] | None, patch: dict[str, Any]) -> dict[str, Any]:
    base = coding_agents_dict(type("S", (), {"coding_agents": existing or {}})())
    if not isinstance(patch, dict):
        return base
    known = set(base) | set(_registered_defaults())
    for key, val in patch.items():
        aid = str(key or "").strip().lower().replace("-", "_")
        if aid not in known or not isinstance(val, dict):
            continue
        row = dict(base.get(aid) or {"enabled": True, "cli_path": "", "default_args": ""})
        if "enabled" in val:
            row["enabled"] = bool(val["enabled"])
        if "cli_path" in val:
            row["cli_path"] = str(val.get("cli_path") or "")
        if "default_args" in val:
            row["default_args"] = str(val.get("default_args") or "")
        if "permission_mode" in val:
            row["permission_mode"] = str(val.get("permission_mode") or "")
        base[aid] = row
    return base
