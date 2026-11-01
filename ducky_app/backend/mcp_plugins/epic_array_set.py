"""Split Epic SetProperties (42.x: set_properties) writes that Unreal's array differ refuses.

SetObjectProperties raises
``ArrayAdd: elements changed alongside the size change; insertion points are ambiguous``
when ``perInstanceSMData`` (or custom instance data) changes length and values in
one call. Empty the array first, then write the new elements.

ponytail: only these two HISM/ISM instance arrays are split. Other ArrayAdd
ambiguities still need a manual clear — widening this would blank unrelated
arrays (materials, components) if the second write failed.
"""

from __future__ import annotations

import json
from typing import Any

_MARKER = "# ducky:instance-array-clear\n"
_INSTANCE_ARRAYS = frozenset({"perinstancesmdata", "perinstancesmcustomdata"})

# 43.00 sandbox: imports only at top level, execute_tool is async.
_PREAMBLE = (
    _MARKER
    + """import json as _json

_ducky_orig_execute_tool = execute_tool

def _ducky_instance_array(key):
    return str(key).replace("_", "").lower() in ("perinstancesmdata", "perinstancesmcustomdata")

async def execute_tool(name, args=None, *a, **k):
    def _loads(v):
        if not isinstance(v, str):
            return v, False
        try:
            return _json.loads(v), True
        except Exception:
            return None, True
    parsed, encoded = _loads(args)
    if not isinstance(parsed, dict) or "setproperties" not in str(name).replace("_", "").lower():
        return await _ducky_orig_execute_tool(name, args, *a, **k)
    values, values_encoded = _loads(parsed.get("values"))
    if not isinstance(values, dict):
        return await _ducky_orig_execute_tool(name, args, *a, **k)
    keys = [key for key, val in values.items() if _ducky_instance_array(key) and isinstance(val, list) and val]
    if not keys:
        return await _ducky_orig_execute_tool(name, args, *a, **k)
    clear = dict(parsed)
    empty = {key: [] for key in keys}
    clear["values"] = _json.dumps(empty) if values_encoded else empty
    payload = _json.dumps(clear) if encoded else clear
    cleared = await _ducky_orig_execute_tool(name, payload, *a, **k)
    text = cleared if isinstance(cleared, str) else str(cleared)
    if "could not be set" in text or "ArrayAdd" in text:
        return cleared
    return await _ducky_orig_execute_tool(name, args, *a, **k)
"""
)


def _is_instance_array_key(key: object) -> bool:
    return str(key).replace("_", "").lower() in _INSTANCE_ARRAYS


def _loads_maybe(value: Any) -> tuple[Any, bool]:
    if not isinstance(value, str):
        return value, False
    try:
        return json.loads(value), True
    except Exception:
        return None, True


def _leaf(tool_name: str) -> str:
    """Last name part, case and underscores folded (43.00 SetProperties == 42.x set_properties)."""
    return tool_name.rsplit(".", 1)[-1].replace("_", "").lower()


def guard_instance_array_script(script: str) -> str:
    """Prepend a set_properties wrapper when a script writes instance arrays."""
    if _MARKER in script:
        return script
    flat = script.replace("_", "").lower()
    if "setproperties" not in flat or "perinstancesmdata" not in flat:
        return script
    return _PREAMBLE + script


def _clear_values(values: Any) -> Any | None:
    """Return values with non-empty instance arrays replaced by [], or None."""
    obj, encoded = _loads_maybe(values)
    if not isinstance(obj, dict):
        return None
    keys = [
        key
        for key, val in obj.items()
        if _is_instance_array_key(key) and isinstance(val, list) and val
    ]
    if not keys:
        return None
    empty = {key: [] for key in keys}
    return json.dumps(empty) if encoded else empty


def _clear_set_properties_arguments(arguments: dict[str, Any]) -> dict[str, Any] | None:
    cleared_values = _clear_values(arguments.get("values"))
    if cleared_values is None:
        return None
    nxt = dict(arguments)
    nxt["values"] = cleared_values
    return nxt


def expand_epic_call(mcp_tool: str, args: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn one Epic ``call_tool`` into the calls Unreal will accept.

    ``set_properties`` with a non-empty instance array becomes clear-then-set.
    ``execute_tool_script`` scripts that do the same get a wrapper prepended.
    """
    if mcp_tool != "call_tool" or not isinstance(args, dict):
        return [args]
    tool = str(args.get("tool_name") or "")
    leaf = _leaf(tool)
    inner, inner_encoded = _loads_maybe(args.get("arguments"))
    if not isinstance(inner, dict):
        return [args]
    if leaf == "executetoolscript":
        script = inner.get("script")
        block = script if isinstance(script, dict) else None  # 43.00: {content, language}
        source = block.get("content") if block else script
        if not isinstance(source, str):
            return [args]
        guarded = guard_instance_array_script(source)
        if guarded == source:
            return [args]
        copied_inner = dict(inner)
        copied_inner["script"] = {**block, "content": guarded} if block else guarded
        copied = dict(args)
        copied["arguments"] = json.dumps(copied_inner) if inner_encoded else copied_inner
        return [copied]
    if leaf != "setproperties":
        return [args]
    cleared = _clear_set_properties_arguments(inner)
    if cleared is None:
        return [args]
    first = dict(args)
    first["arguments"] = json.dumps(cleared) if inner_encoded else cleared
    return [first, args]
