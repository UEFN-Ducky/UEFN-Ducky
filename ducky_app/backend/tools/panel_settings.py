"""Read and write panel settings (panel_settings.json) from an agent.

Wraps :class:`~frontend.settings.PanelSettings`. Writes are whitelisted against
:mod:`frontend.settings_schema` (only fields flagged ``settable``), validated,
and gated by the ``allow_settings_write`` master switch. This file holds no
secrets — API keys are rejected on load — so there is nothing to redact.
"""

from __future__ import annotations

from typing import Any

from frontend.settings import PanelSettings
from frontend.settings_schema import FIELD_META, settable_keys, settings_schema

from backend.json_util import tool_json
from backend.server import mcp


def _coerce(name: str, value: Any) -> Any:
    """Coerce a patch value to the field's declared type; raise on bad enum/int."""
    schema_field = settings_schema()["fields"].get(name, {})
    ftype = schema_field.get("type", "object")
    enum = schema_field.get("enum")
    if ftype == "bool":
        if isinstance(value, bool):
            out: Any = value
        elif isinstance(value, (int, float)):
            out = bool(value)
        elif isinstance(value, str):
            out = value.strip().lower() in ("1", "true", "yes", "on")
        else:
            raise ValueError(f"{name}: expected bool")
    elif ftype == "int":
        try:
            out = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name}: expected int") from exc
    elif ftype == "str":
        out = str(value)
    else:
        out = value
    if enum is not None and out not in enum:
        raise ValueError(f"{name}: must be one of {enum}")
    return out


def apply_settings_patch(patch: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    """Validate + apply a settings patch. Returns {before, after, restart_required}.

    Shared by ducky_settings_set and the guide composer. Never raises for a
    caller-facing error — returns ``{"error": ...}`` instead.
    """
    if not isinstance(patch, dict) or not patch:
        return {"error": "patch must be a non-empty object"}
    settings = PanelSettings.load()
    if not settings.allow_settings_write:
        return {"error": "settings writes disabled (allow_settings_write is off — re-enable in Settings)"}
    allowed = settable_keys()
    unknown = [k for k in patch if k not in allowed]
    if unknown:
        return {"error": f"not settable: {unknown}", "settable": sorted(allowed)}

    before: dict[str, Any] = {}
    coerced: dict[str, Any] = {}
    try:
        for key, value in patch.items():
            before[key] = getattr(settings, key)
            coerced[key] = _coerce(key, value)
    except ValueError as exc:
        return {"error": str(exc)}

    for key, value in coerced.items():
        setattr(settings, key, value)
    try:
        settings.validate()
    except ValueError as exc:
        return {"error": f"invalid: {exc}"}

    restart = [k for k in coerced if getattr(FIELD_META.get(k), "restart_required", False)]
    after = {k: getattr(settings, k) for k in coerced}
    if dry_run:
        return {"before": before, "after": after, "restart_required": restart, "dry_run": True}

    settings.save()
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event({"type": "settings_changed", "keys": list(coerced)})
    except Exception:
        pass
    return {"before": before, "after": after, "restart_required": restart}


@mcp.tool()
def ducky_settings_schema(pretty: bool = False) -> str:
    """Machine-readable schema of every panel setting.

    Each field: {type, default, label, tab, settable, restart_required, enum?, description?}.
    `settable` marks fields ducky_settings_set may write. Read this before
    ducky_settings_set so you use exact key names and valid enum values.
    """
    return tool_json(settings_schema(), pretty=pretty)


@mcp.tool()
def ducky_settings_get(keys: list[str] | None = None, pretty: bool = False) -> str:
    """Read current panel settings (from panel_settings.json).

    Pass `keys` to return only those fields, else all. Holds no secrets.
    Example: ducky_settings_get(["agent_provider", "tool_result_format"]).
    """
    data = PanelSettings.load().to_json_dict()
    if keys:
        wanted = [str(k) for k in keys]
        data = {k: v for k, v in data.items() if k in wanted}
    return tool_json({"settings": data}, pretty=pretty)


@mcp.tool()
def ducky_settings_set(patch: dict[str, Any], dry_run: bool = False, pretty: bool = False) -> str:
    """Write panel settings by key. Only schema-`settable` fields are allowed.

    patch: {key: value} (e.g. {"tool_result_format": "json"}).
    dry_run: validate + preview {before, after} without saving.
    Returns {before, after, restart_required}. Refused when allow_settings_write is off.
    Values are type-checked and enum-validated; the change hot-applies to open Settings.
    """
    return tool_json(apply_settings_patch(patch or {}, dry_run=bool(dry_run)), pretty=pretty)
