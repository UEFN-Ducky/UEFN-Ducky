"""Creative / Verse device settings editor for UEFN Ducky.

Fortnite Creative devices (player spawners, triggers, timers, etc.) expose gameplay
settings through ToyOptionsComponent property overrides. Most settings are also
readable/writable via ``actor.set_editor_property(name, value)`` when values are
coerced to the correct Unreal types (enums, structs, bools).

Verse ``@editable`` device references use mangled names on the inner ``Script``
subobject — see ``verse_editable_editor`` (not the actor wrapper or human field names).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import unreal

from listener import lookup
from listener.save_coalesce import request_level_save

_SKIP_OPTION_KEYS = frozenset({
    "Enable", "Disable", "SpawnPlayer", "On Player Spawned", "OnSpawnFailed",
})

_CREATIVE_CLASS_MARKERS = (
    "FortCreativeDeviceProp",
    "VerseDevice_C",
    "BP_Creative_",
    "Creative_",
    # Island / Experience Settings (Device_ExperienceSettings_V2_UEFN_C) — no Creative_ prefix
    "ExperienceSettings",
)


def _overrides_map(comp: unreal.ActorComponent) -> Dict[str, str]:
    raw = comp.get_property_overrides()
    return dict(raw) if raw else {}


def _get_toy_options(actor: unreal.Actor) -> Optional[unreal.ActorComponent]:
    for comp in actor.get_components_by_class(unreal.ActorComponent):
        if "ToyOptions" in comp.get_class().get_name():
            return comp
    return None


def is_creative_device(actor: unreal.Actor) -> bool:
    cls = actor.get_class().get_name()
    if any(m in cls for m in _CREATIVE_CLASS_MARKERS):
        return True
    # ToyOptions holders (Island Settings, some companions) without a class marker
    return _get_toy_options(actor) is not None


def _device_kind(actor: unreal.Actor) -> str:
    cls = actor.get_class().get_name()
    if cls == "VerseDevice_C":
        return "verse_script"
    if "BP_Creative_" in cls or "FortCreative" in cls or "ExperienceSettings" in cls:
        return "creative_device"
    if _get_toy_options(actor) is not None:
        return "creative_device"
    return "actor"


def _enum_members(enum_cls: type) -> List[str]:
    return [n for n in dir(enum_cls) if n.isupper() and not n.startswith("_")]


def _coerce_enum(current: Any, raw: Any) -> Any:
    enum_cls = current.__class__
    if isinstance(raw, enum_cls):
        return raw
    text = str(raw).strip()
    if "." in text:
        text = text.split(".")[-1]
    text = text.upper().replace(" ", "_")
    if hasattr(enum_cls, text):
        return getattr(enum_cls, text)
    for member in _enum_members(enum_cls):
        if member.upper() == text:
            return getattr(enum_cls, member)
    raise ValueError(
        f"Unknown enum value {raw!r} for {enum_cls.__name__}. Valid: {_enum_members(enum_cls)}"
    )


def _parse_struct_fields(text: str) -> Dict[str, str]:
    text = text.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    out: Dict[str, str] = {}
    for part in re.split(r",\s*", text):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _coerce_struct(current: Any, raw: Any) -> Any:
    if isinstance(raw, dict):
        fields = {str(k): v for k, v in raw.items()}
    elif isinstance(raw, str) and ("=" in raw or raw.startswith("(")):
        fields = _parse_struct_fields(raw)
    else:
        raise ValueError(f"Struct value must be dict or '(Field=Value,...)' string, got {raw!r}")

    struct = current
    if hasattr(unreal, type(current).__name__):
        try:
            struct = getattr(unreal, type(current).__name__)()
        except Exception:
            pass

    key_map = {
        "teamtype": "team_type",
        "teamindex": "team_index",
        "classtype": "class_type",
        "classslot": "class_slot",
    }
    for key, val in fields.items():
        prop = key_map.get(key.lower().replace("_", ""), key)
        if prop == key and key[0].isupper():
            prop = re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()
        try:
            existing = struct.get_editor_property(prop)
        except Exception:
            existing = None
        if existing is not None and hasattr(existing, "__class__") and hasattr(existing.__class__, "__members__"):
            struct.set_editor_property(prop, _coerce_enum(existing, val))
        elif isinstance(existing, bool) or str(val).lower() in ("true", "false"):
            struct.set_editor_property(prop, str(val).lower() in ("true", "1", "yes"))
        elif isinstance(existing, int) or (isinstance(val, str) and val.isdigit()):
            struct.set_editor_property(prop, int(val))
        else:
            try:
                cur = struct.get_editor_property(prop)
                struct.set_editor_property(prop, _coerce_enum(cur, val))
            except Exception:
                struct.set_editor_property(prop, val)
    return struct


def _coerce_value(current: Any, raw: Any) -> Any:
    if current is None:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return raw
        text = str(raw).strip().lower()
        if text in ("true", "1", "yes"):
            return True
        if text in ("false", "0", "no"):
            return False
        try:
            return int(raw)
        except (TypeError, ValueError):
            try:
                return float(raw)
            except (TypeError, ValueError):
                return raw

    if isinstance(current, bool):
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("true", "1", "yes")

    if isinstance(current, int) and not isinstance(current, bool):
        return int(raw)

    if isinstance(current, float):
        return float(raw)

    if isinstance(current, str):
        return str(raw)

    cls_name = type(current).__name__
    if cls_name.endswith("Type") or (hasattr(current, "name") and hasattr(current, "value")):
        return _coerce_enum(current, raw)

    if hasattr(current, "get_editor_property"):
        return _coerce_struct(current, raw)

    return raw


def _serialize_option_value(value: Any, override_str: Optional[str] = None) -> dict:
    info: dict = {"python_type": type(value).__name__}
    if override_str is not None:
        info["override"] = override_str
    if isinstance(value, bool):
        info["value"] = value
        info["type"] = "bool"
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        info["value"] = value
        info["type"] = "number"
    elif isinstance(value, str):
        info["value"] = value
        info["type"] = "string"
    elif hasattr(value, "name"):
        info["value"] = value.name
        info["enum_class"] = type(value).__name__
        info["type"] = "enum"
        info["enum_values"] = _enum_members(type(value))
    elif hasattr(value, "get_editor_property"):
        info["type"] = "struct"
        info["value"] = str(value)
        if override_str:
            info["value"] = override_str
        fields: dict = {}
        for field in ("team_type", "team_index", "class_type", "class_slot"):
            try:
                fields[field] = str(value.get_editor_property(field))
            except Exception:
                pass
        if fields:
            info["fields"] = fields
    else:
        info["value"] = str(value)
        info["type"] = "unknown"
    return info


def _option_schema_entry(option_obj: Any) -> dict:
    entry = {
        "key": str(option_obj.get_editor_property("option_key")),
        "display_name": str(getattr(option_obj, "option_display_name", "")),
        "description": str(getattr(option_obj, "option_description", "")),
    }
    if hasattr(option_obj, "option_values"):
        try:
            vals = option_obj.option_values
            entry["allowed_values"] = [
                str(getattr(v, "option_value_name", v)) for v in vals
            ]
        except Exception:
            pass
    return entry


def list_creative_devices(
    class_filter: str = "",
    label_filter: str = "",
    limit: int = 200,
) -> dict:
    devices = []
    for actor in lookup.actor_list():
        if not is_creative_device(actor):
            continue
        cls = actor.get_class().get_name()
        label = actor.get_actor_label()
        if class_filter and class_filter not in cls:
            continue
        if label_filter and label_filter.lower() not in label.lower():
            continue
        kind = _device_kind(actor)
        comp = _get_toy_options(actor)
        option_count = len(_overrides_map(comp)) if comp else 0
        row = {
            "label": label,
            "class": cls,
            "path": actor.get_path_name(),
            "kind": kind,
            "option_count": option_count,
        }
        if kind == "verse_script":
            try:
                script = actor.get_editor_property("Script")
                if script is not None:
                    row["script_class"] = script.get_class().get_name()
            except Exception:
                pass
        devices.append(row)
        if len(devices) >= limit:
            break
    return {"devices": devices, "count": len(devices)}


def get_device_settings(
    actor_path: str,
    include_events: bool = False,
    keys: list | None = None,
) -> dict:
    actor = lookup.require_actor(actor_path)
    comp = _get_toy_options(actor)
    if comp is None:
        raise ValueError(f"Actor has no ToyOptionsComponent: {actor.get_actor_label()}")

    overrides = _overrides_map(comp)
    definitions = {}
    try:
        for opt in comp.get_user_option_definitions():
            entry = _option_schema_entry(opt)
            definitions[entry["key"]] = entry
    except Exception:
        pass

    wanted = [k for k in (keys or []) if isinstance(k, str) and k.strip()]
    wanted_fold = {k.casefold() for k in wanted}

    settings: Dict[str, dict] = {}
    for key, override_str in overrides.items():
        if not include_events and key in _SKIP_OPTION_KEYS:
            continue
        if wanted and key not in wanted and key.casefold() not in wanted_fold:
            continue
        try:
            current = actor.get_editor_property(key)
            settings[key] = _serialize_option_value(current, override_str)
            if key in definitions:
                settings[key]["display_name"] = definitions[key].get("display_name", "")
                if "allowed_values" in definitions[key]:
                    settings[key]["allowed_values"] = definitions[key]["allowed_values"]
        except Exception as exc:
            settings[key] = {
                "type": "readonly_override",
                "value": override_str,
                "error": str(exc)[:200],
            }

    out = {
        "actor_path": actor.get_path_name(),
        "label": actor.get_actor_label(),
        "class": actor.get_class().get_name(),
        "kind": _device_kind(actor),
        "description": str(getattr(comp, "options_description", "")),
        "settings": settings,
        "writable_via_python": True,
        "notes": (
            "Verse @editable references (PlayerManager, etc.) are NOT available via Python. "
            "Use Verse code or the Details panel for those."
            if _device_kind(actor) == "verse_script"
            else None
        ),
    }
    if wanted:
        out["keys_filtered"] = True
        missing = [
            k
            for k in wanted
            if k not in settings and k.casefold() not in {s.casefold() for s in settings}
        ]
        if missing:
            out["keys_missing"] = missing
    return out


def _apply_device_settings(actor: unreal.Actor, comp: unreal.ActorComponent, properties: Dict[str, Any]) -> Dict[str, dict]:
    """Set each option and report before/after — no transaction (caller owns it)."""
    results: Dict[str, dict] = {}
    for key, raw in properties.items():
        try:
            before_override = _overrides_map(comp).get(key)
            try:
                current = actor.get_editor_property(key)
            except Exception:
                current = None
            coerced = _coerce_value(current, raw)
            actor.set_editor_property(key, coerced)
            actor.modify()
            comp.refresh_property_overrides()
            after_override = _overrides_map(comp).get(key)
            results[key] = {
                "ok": True,
                "before": before_override,
                "set": str(coerced),
                "after": after_override,
            }
            if after_override is None:
                try:
                    results[key]["after"] = str(actor.get_editor_property(key))
                except Exception:
                    pass
        except Exception as exc:
            results[key] = {"ok": False, "error": str(exc)}
    return results


def set_device_settings(
    actor_path: str,
    properties: Dict[str, Any],
    save_level: bool = False,
) -> dict:
    actor = lookup.require_actor(actor_path)
    comp = _get_toy_options(actor)
    if comp is None:
        raise ValueError(f"Actor has no ToyOptionsComponent: {actor.get_actor_label()}")

    with unreal.ScopedEditorTransaction("MCP Set Device Settings"):
        results = _apply_device_settings(actor, comp, properties)

    lookup.invalidate()

    if save_level:
        request_level_save()

    return {
        "actor_path": actor.get_path_name(),
        "label": actor.get_actor_label(),
        "results": results,
        "success_count": sum(1 for r in results.values() if r.get("ok")),
        "error_count": sum(1 for r in results.values() if not r.get("ok")),
    }


def bulk_set_device_settings(
    properties: Dict[str, Any],
    class_filter: str = "",
    label_filter: str = "",
    save_level: bool = False,
) -> dict:
    matched = []
    for actor in lookup.actor_list():
        if not is_creative_device(actor):
            continue
        cls = actor.get_class().get_name()
        label = actor.get_actor_label()
        if class_filter and class_filter not in cls:
            continue
        if label_filter and label_filter.lower() not in label.lower():
            continue
        matched.append(actor)

    all_results = []
    with unreal.ScopedEditorTransaction("MCP Bulk Set Device Settings"):
        for actor in matched:
            comp = _get_toy_options(actor)
            if comp is None:
                all_results.append({
                    "actor_path": actor.get_path_name(),
                    "label": actor.get_actor_label(),
                    "results": {},
                    "success_count": 0,
                    "error_count": 0,
                })
                continue
            results = _apply_device_settings(actor, comp, properties)
            all_results.append({
                "actor_path": actor.get_path_name(),
                "label": actor.get_actor_label(),
                "results": results,
                "success_count": sum(1 for r in results.values() if r.get("ok")),
                "error_count": sum(1 for r in results.values() if not r.get("ok")),
            })

    lookup.invalidate()

    if save_level:
        request_level_save()

    return {
        "matched": len(matched),
        "devices": [r["label"] for r in all_results],
        "results": all_results,
    }
