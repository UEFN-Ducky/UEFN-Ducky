"""Record plugin / Epic MCP tool results into the change journal.

One choke point used by the in-app agent (`execute_tool`) and the IDE bridge
(`plugin_gate`). Never raises: a bookkeeping failure must not fail the tool.
"""

from __future__ import annotations

import itertools
import json
import threading
from typing import Any, Mapping

from backend.workspace.editor_ops import slot_path
from backend.workspace.editor_record import record, record_sidecar_text
from backend.workspace.identity import current_writer

_SKIP = frozenset({"ducky_call_tool"})
_EPIC_CREATE = frozenset({"PlaceDevice", "SpawnActor", "CreateEntity", "InstantiatePrefab"})
_READ_PREFIXES = ("Get", "List", "Describe", "Find", "Query", "Is", "Has", "Can")
# Plan-mode allowlists these so agents can tick a plan; they still mutate Ducky.
_PLAN_MUTATORS = frozenset({
    "ducky_create_plan",
    "ducky_update_plan",
    "ducky_plan_add_node",
    "ducky_plan_update_node",
    "ducky_plan_delete_node",
    "ducky_plan_move_node",
    "ducky_create_plan_template",
    "ducky_update_plan_template",
    "ducky_instantiate_plan_template",
})

_opaque_counts: dict[str, int] = {}
_opaque_lock = threading.Lock()
_opaque_fallback = itertools.count(1)
_OPAQUE_RUNS_KEPT = 256


def record_tool_result(
    name: str,
    args: Mapping[str, Any] | None,
    text: str,
    *,
    ok: bool,
) -> None:
    """Journal a plugin / Epic result. Never raises."""
    try:
        _record((name or "").strip(), dict(args or {}), text or "", ok=ok)
    except Exception:
        pass


def _record(name: str, args: dict[str, Any], text: str, *, ok: bool) -> None:
    if not name or name in _SKIP or name.startswith("changeset_"):
        return
    if _is_read(name):
        return
    if record_sidecar_text(name, args, text, ok=ok):
        return
    if name == "unreal__call_tool":
        _record_epic(args, text, ok=ok)
        return
    if _already_journaled(name):
        return
    _record_generic(name, args, text, ok=ok)


def _is_read(name: str) -> bool:
    if name in _PLAN_MUTATORS:
        return False
    try:
        from backend.agent.toolsets.plan_safe import is_plan_safe_tool

        return is_plan_safe_tool(name)
    except Exception:
        return False


def _already_journaled(name: str) -> bool:
    """True when send_command / ProjectWriter already wrote the row.

    ponytail: a listener command missing from EDITOR_OPS gets two rows; add it
    to the table. Upgrade: classify every listener command, drop this skip.
    """
    from backend.workspace.editor_ops import _UNKNOWN_NOTE, classify
    from backend.workspace.lanes import PATH_ARGS

    if name in PATH_ARGS:
        return True
    return classify(name).note != _UNKNOWN_NOTE


def _plugin_for_tool(name: str) -> str:
    try:
        from backend.uefn_plugins.host import plugin_for_tool

        return plugin_for_tool(name) or "ducky"
    except Exception:
        return "ducky"


def _short_args(args: dict[str, Any]) -> str:
    if not args:
        return ""
    try:
        blob = json.dumps(args, ensure_ascii=False, default=str)
    except Exception:
        blob = repr(args)
    return blob[:120]


def _record_generic(name: str, args: dict[str, Any], text: str, *, ok: bool) -> None:
    program = _plugin_for_tool(name)
    run_id = str(current_writer(tool=name).get("run_id") or "")
    sidecar = {
        "program": program,
        "kind": "tool",
        "facet": "call",
        "slot": _opaque_slot(run_id, name, kind="tool", program=program),
        "targets": [{"kind": "tool", "id": name, "label": name, "path": name}],
        "revertable": "none",
        "reason": (
            f"{program} did not report how to undo this; "
            "return _ducky (inverse/created) to make it revertable"
        ),
        "summary": f"{name} {_short_args(args)}".strip(),
        "after": {"result": (text or "")[:2000]},
    }
    record(name, args, {"_ducky": sidecar}, ok=ok)


def _is_epic_read(tool: str) -> bool:
    return any(tool.startswith(prefix) for prefix in _READ_PREFIXES)


def _opaque_slot(run_id: str, leaf: str, *, kind: str = "epic", program: str = "uefn") -> str:
    if not run_id:
        n = next(_opaque_fallback)
        return slot_path(kind, f"{leaf}/user-{n}", program=program)
    with _opaque_lock:
        count = _opaque_counts.get(run_id, 0) + 1
        _opaque_counts[run_id] = count
        if len(_opaque_counts) > _OPAQUE_RUNS_KEPT:
            for stale in list(_opaque_counts)[: len(_opaque_counts) - _OPAQUE_RUNS_KEPT]:
                _opaque_counts.pop(stale, None)
    return slot_path(kind, f"{leaf}/{run_id}-{count}", program=program)


def _record_epic(args: dict[str, Any], text: str, *, ok: bool) -> None:
    tool = str(args.get("tool_name") or "").strip()
    if not tool or _is_epic_read(tool):
        return
    inner = args.get("arguments") if isinstance(args.get("arguments"), dict) else {}
    toolset = str(args.get("toolset_name") or "").strip()
    run_id = str(current_writer(tool=tool).get("run_id") or "")

    if tool in _EPIC_CREATE:
        created = _epic_created_targets(text, inner)
        kind = "entity" if ("Entity" in tool or "Prefab" in tool) else "actor"
        label = (created[0].get("label") or created[0].get("path") or tool) if created else tool
        sidecar: dict[str, Any] = {
            "created": created,
            "targets": created,
            "kind": kind,
            "facet": "exists",
            "revertable": "auto" if created else "manual",
            "program": "uefn",
            "summary": f"placed {label}",
        }
        command = "spawn_actor" if tool in {"PlaceDevice", "SpawnActor"} else tool
        record(command, inner, {"_ducky": sidecar}, ok=ok)
        return

    if tool == "execute_tool_script":
        script = inner.get("script") if isinstance(inner, dict) else ""
        sidecar = {
            "program": "uefn",
            "kind": "world",
            "facet": "opaque",
            "slot": _opaque_slot(run_id, "execute_tool_script"),
            "revertable": "manual",
            "summary": "execute_tool_script",
            "reason": "Epic programmatic script — effects are not modelled",
            "before": {"code": script} if isinstance(script, str) and script.strip() else None,
        }
        record("execute_tool_script", inner, {"_ducky": sidecar}, ok=ok)
        return

    ident = _epic_target_ident(inner, text)
    kind, facet = _epic_kind_facet(tool)
    sidecar = {
        "program": "uefn",
        "kind": kind,
        "facet": facet,
        "revertable": "manual",
        "summary": f"{tool} {ident}".strip(),
        "targets": (
            [{"kind": kind, "id": ident, "label": ident, "path": ident}] if ident else []
        ),
        "slot": (
            slot_path(kind, ident, facet, program="uefn")
            if ident
            else _opaque_slot(run_id, f"{toolset}.{tool}" if toolset else tool)
        ),
    }
    record(tool, inner, {"_ducky": sidecar}, ok=ok)


def _epic_target_ident(inner: dict[str, Any], text: str) -> str:
    blob = _parse_json(text)
    for src in (blob, inner):
        if not isinstance(src, dict):
            continue
        for key in (
            "actor_path",
            "entity_path",
            "device",
            "asset_path",
            "path",
            "ActorPath",
            "label",
            "name",
        ):
            value = src.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _epic_kind_facet(tool: str) -> tuple[str, str]:
    low = tool.lower()
    if "material" in low:
        return "material", "props"
    if "niagara" in low or "particle" in low:
        return "niagara", "props"
    if "widget" in low or "umg" in low:
        return "umg", "props"
    if "entity" in low or "prefab" in low:
        return "entity", "props"
    if "device" in low:
        return "device", "settings"
    if "transform" in low:
        return "actor", "transform"
    return "actor", "props"


def _epic_created_targets(text: str, inner: dict[str, Any]) -> list[dict[str, str]]:
    blob = _parse_json(text)
    path = str(
        blob.get("actor_path")
        or blob.get("path")
        or blob.get("ActorPath")
        or blob.get("entity_path")
        or inner.get("label")
        or inner.get("name")
        or "",
    ).strip()
    label = str(blob.get("label") or blob.get("name") or inner.get("label") or path).strip()
    guid = str(blob.get("guid") or blob.get("Guid") or "").strip()
    if not path and not label:
        return []
    ident = path or label
    kind = "entity" if blob.get("entity_path") or inner.get("entity_path") else "actor"
    return [{"kind": kind, "id": ident, "guid": guid, "label": label or ident, "path": ident}]


def _parse_json(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip().startswith("{"):
        return {}
    try:
        obj = json.loads(text)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}
