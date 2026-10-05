"""Typed pins: what a node takes in and gives out, and which wires fit.

The panel mirrors these rules in ``automations/pins.ts``; keep the two in step.
"""

from __future__ import annotations

from typing import Any, Callable

PIN_TYPES = ("text", "number", "boolean", "json", "any", "image", "images", "audio", "video", "mesh", "svg", "pdf", "file")
FILE_TYPES = frozenset({"image", "audio", "video", "mesh", "svg", "pdf", "file"})
DATA_KIND = "data"

# Run workflow / Inputs / Return / If / Expression / Template build their pins from settings.
_NAMED_INPUT_TYPES = {"logic.if": "any", "logic.expression": "any", "text.template": "text", "list.make": "any"}
_NAMED_DEFAULTS = {"logic.if": ["value"], "logic.expression": ["a", "b"], "text.template": ["a", "b"], "list.make": ["a", "b"]}
# Nodes whose `names` setting adds one output per name (Extract data: one per field).
_NAMED_OUTPUT_TYPES = {"llm.extract": "any"}


def clean_type(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    return value if value in PIN_TYPES else "any"


def clean_pins(raw: Any) -> list[dict[str, Any]]:
    """A pin list from a catalog row or plugin.json: id, label, type (+ required, default, description)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("id") or "").strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        pin: dict[str, Any] = {"id": pid, "label": str(row.get("label") or pid), "type": clean_type(row.get("type"))}
        if row.get("required"):
            pin["required"] = True
        if "default" in row:
            pin["default"] = row["default"]
        if row.get("description"):
            pin["description"] = str(row["description"])
        out.append(pin)
    return out


def accepts(target: str, source: str) -> bool:
    """Whether a wire from an output of type ``source`` may feed an input of type ``target``."""
    target, source = clean_type(target), clean_type(source)
    if target == source or target in ("any", "json") or source == "any":
        return True
    if target == "text":
        return source in ("number", "boolean")
    if target == "images":
        return source == "image"
    if target == "file":
        return source in FILE_TYPES
    return False


def _names(raw: Any, key: str = "name") -> list[str]:
    out: list[str] = []
    for row in raw if isinstance(raw, list) else []:
        name = str((row.get(key) if isinstance(row, dict) else row) or "").strip()
        if name and name not in out:
            out.append(name)
    return out


def _typed(raw: Any) -> dict[str, str]:
    return {str(row.get("name") or "").strip(): clean_type(row.get("type")) for row in raw if isinstance(row, dict)} if isinstance(raw, list) else {}


def node_pins(
    node: dict[str, Any],
    spec: dict[str, Any] | None,
    signature: Callable[[str], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """``{"exec": bool, "inputs": [...], "outputs": [...]}`` for one node on a graph."""
    spec = spec or {}
    ntype = str(node.get("type") or "")
    cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
    inputs = clean_pins(spec.get("inputs"))
    outputs = clean_pins(spec.get("outputs"))
    if ntype == "code.js":
        # Custom code: the pins its code declares, as the last good check wrote them.
        declared = cfg.get("pins") if isinstance(cfg.get("pins"), dict) else {}
        return {
            "exec": declared.get("exec", True) is not False,
            "inputs": clean_pins(declared.get("inputs")),
            "outputs": clean_pins(declared.get("outputs")),
        }
    if ntype == "flow.input":
        types = _typed(cfg.get("inputs"))
        outputs = [{"id": name, "label": name, "type": types.get(name, "any")} for name in _names(cfg.get("inputs"))]
    elif ntype == "flow.output":
        types = _typed(cfg.get("outputs"))
        inputs = [{"id": name, "label": name, "type": types.get(name, "any")} for name in _names(cfg.get("outputs"))]
    elif ntype == "workflow.call" and signature is not None:
        sig = signature(str(cfg.get("workflow_id") or "")) or {}
        inputs = [{"id": row["name"], "label": row["name"], "type": clean_type(row.get("type"))} for row in sig.get("inputs") or [] if isinstance(row, dict) and row.get("name")]
        outputs = [{"id": name, "label": name, "type": clean_type((sig.get("output_types") or {}).get(name))} for name in sig.get("outputs") or []]
    elif ntype in _NAMED_INPUT_TYPES:
        names = _names(cfg.get("names")) or list(_NAMED_DEFAULTS[ntype])
        inputs = [{"id": name, "label": name, "type": _NAMED_INPUT_TYPES[ntype]} for name in names]
    elif ntype in _NAMED_OUTPUT_TYPES:
        taken = {pin["id"] for pin in outputs}
        outputs = outputs + [{"id": name, "label": name, "type": _NAMED_OUTPUT_TYPES[ntype]} for name in _names(cfg.get("names")) if name not in taken]
    return {"exec": spec.get("exec", True) is not False, "inputs": inputs, "outputs": outputs}


def check_wires(
    graph: dict[str, Any],
    specs: dict[str, dict[str, Any]],
    signature: Callable[[str], dict[str, Any] | None] | None = None,
) -> list[str]:
    """What is wrong with the data wires of a graph: missing pins and types that don't fit."""
    nodes = {str(n.get("id")): n for n in graph.get("nodes") or [] if isinstance(n, dict)}
    problems: list[str] = []

    def label(node: dict[str, Any]) -> str:
        return str(node.get("label") or (specs.get(str(node.get("type"))) or {}).get("label") or node.get("type") or node.get("id"))

    for edge in graph.get("edges") or []:
        if not isinstance(edge, dict) or edge.get("kind") != DATA_KIND:
            continue
        source, target = nodes.get(str(edge.get("source"))), nodes.get(str(edge.get("target")))
        if source is None or target is None:
            continue
        if str(source.get("type")) not in specs or str(target.get("type")) not in specs:
            continue  # a plugin that is off: keep its wires as they are
        out = next((pin for pin in node_pins(source, specs[str(source.get("type"))], signature)["outputs"] if pin["id"] == edge.get("source_pin")), None)
        into = next((pin for pin in node_pins(target, specs[str(target.get("type"))], signature)["inputs"] if pin["id"] == edge.get("target_pin")), None)
        if out is None:
            problems.append(f"{label(source)} has no output {edge.get('source_pin')!r}")
        elif into is None:
            problems.append(f"{label(target)} has no input {edge.get('target_pin')!r}")
        elif not accepts(into["type"], out["type"]):
            problems.append(f"{label(source)}.{out['id']} ({out['type']}) can't feed {label(target)}.{into['id']} ({into['type']})")
    return problems
