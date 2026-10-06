"""The JavaScript a node runs, for its Code tab.

Built-ins keep running their Python handlers; this is what the node would be as
Custom code. Real JS only where it does exactly what the built-in does (Call tool,
the Input nodes, Text template, Expression and the simple list nodes); every other
node calls its own built-in through ``ducky.builtin`` with its settings written in.
Route nodes (If, Branch, loops, starts and ends, Run workflow…) choose where a
workflow goes, which code never does, so they get a read-only explanation instead.
"""

from __future__ import annotations

import json
import re
from typing import Any

from backend.automations.code_check import flow_types

_BARE_KEY = re.compile(r"^[A-Za-z_$][\w$]*$")
_RUN_DOC = '/** @param {Record<string, any>} input @param {import("ducky").Ducky} ducky */'
_RESULT_NOTE = ("As code, this step's result no longer lands in the run's fields ({{field}}); "
                "later steps read its outputs as {{nodes.<id>.<pin>}}.")
_TYPE_NOTES = {
    "notify.message": "It no longer posts when the run fails before it (the built-in's \"Also if the run fails before here\").",
    "pipeline.agent": ("A run opens its ducky group only for built-in Agent nodes, so with none left "
                       "this ducky works outside that group."),
    "ducky.spawn": ("Run from a chat, a workflow opens a ducky group only for built-in Agent or Spawn ducky nodes, "
                    "so with none left the new ducky works outside that group."),
    "tool.call": "Result.output is the tool's parsed result here (the built-in kept its raw text).",
}
_FLOW_DOCS = {
    "start.manual": "Starts the workflow when you press Test or run_workflow runs it.",
    "start.cron": "Starts the workflow on its schedule while UEFN Ducky is open.",
    "start.chat": "Starts the workflow from a chat and passes the chat's text and files on.",
    "spotlight.step": "Starts the workflow when a desktop spotlight reaches a step.",
    "spotlight.closed": "Starts the workflow when a desktop spotlight ends.",
    "flow.input": "Starts a reusable workflow: the values a Run workflow node passes in arrive here.",
    "flow.output": "Ends this path and hands its values back to the workflow that ran this one.",
    "flow.end": "Ends this path without posting a reply.",
    "pipeline.finish": "Ends this path and sends its result and files back to the user.",
    "flow.branch": "Follows the True or the False wire, from a field of the run or a judge ducky's answer.",
    "logic.if": "Checks its condition, then follows the True or the False wire.",
    "flow.foreach": "Runs the Each wire once per item of a list, then follows Done.",
    "flow.repeat": "Runs the Each try wire until Until says yes or the tries run out, then follows Done.",
    "workflow.call": "Runs another workflow like a function; if it has Return nodes and none is reached, this path stops.",
    "fortnite.servers": "Checks Epic's servers (waiting while they are down), then follows True (up) or False (still down).",
    "util.preview": "Shows what is wired in on its card; Use this runs the steps that take it.",
    "code.js": "This node is custom code already.",
}

# Helpers the generated code carries along, only when it uses them.
_HELPERS = {
    "asList": '''/** A value as a list, like the built-in: a list, JSON text of a list, an object's values, else one item. */
function asList(value) {
  if (Array.isArray(value)) return value;
  if (value === null || value === undefined || value === "") return [];
  if (typeof value === "string" && value.trim().startsWith("[")) {
    try {
      const parsed = JSON.parse(value);
      if (Array.isArray(parsed)) return parsed;
    } catch (_) {
      // not a list after all: one item
    }
  }
  if (typeof value === "object" && !value.path) return Object.values(value);
  return [value];
}''',
    "asNumber": '''/** A value as a number, like the built-in (yes/no count as 1/0, blank as 0). */
function asNumber(value) {
  if (typeof value === "boolean") return value ? 1 : 0;
  if (typeof value === "number") return value;
  if (value === null || value === undefined) return 0;
  const text = String(value).trim();
  return text === "" ? NaN : Number(text);
}''',
    "asText": '''/** A value as text, like the built-in: blank for nothing, JSON for lists and objects. */
function asText(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return pyJson(value);
  return String(value);
}''',
    "pyText": '''/** A value as text, like the built-in template: True/False, JSON for lists and objects. */
function pyText(value) {
  if (value === null || value === undefined) return "";
  if (value === true) return "True";
  if (value === false) return "False";
  if (typeof value === "object") return pyJson(value);
  return String(value);
}''',
    "pyJson": '''/** JSON spaced the way the built-in writes it ("a": 1, "b": 2). */
function pyJson(value) {
  if (Array.isArray(value)) return "[" + value.map(pyJson).join(", ") + "]";
  if (value && typeof value === "object") {
    return "{" + Object.entries(value).map(([key, item]) => JSON.stringify(key) + ": " + pyJson(item)).join(", ") + "}";
  }
  return JSON.stringify(value) ?? "null";
}''',
    "fill": '''/** {{placeholders}} in every text of the arguments, like the Call tool node. */
async function fill(value, ducky) {
  if (typeof value === "string") return ducky.template(value);
  if (Array.isArray(value)) return Promise.all(value.map((item) => fill(item, ducky)));
  if (value && typeof value === "object") {
    const out = {};
    for (const [key, item] of Object.entries(value)) out[key] = await fill(item, ducky);
    return out;
  }
  return value;
}''',
}
_HELPER_NEEDS = {"asText": ("pyJson",), "pyText": ("pyJson",)}


def js(value: Any, indent: int = 0) -> str:
    """A JS literal for plain data: bare keys where they can be, JSON strings."""
    pad, inner = "  " * indent, "  " * (indent + 1)
    if isinstance(value, dict):
        if not value:
            return "{}"
        rows = [f"{inner}{k if _BARE_KEY.match(str(k)) else json.dumps(str(k), ensure_ascii=False)}: {js(v, indent + 1)}," for k, v in value.items()]
        return "{\n" + "\n".join(rows) + f"\n{pad}}}"
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        return "[\n" + "\n".join(f"{inner}{js(v, indent + 1)}," for v in value) + f"\n{pad}]"
    return _scalar(value)


def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float) and value != value:
        return "null"
    return json.dumps(value, ensure_ascii=False)


def _inline(value: Any) -> str:
    """One pin on one line: { id: "text", type: "text", label: "Text" }."""
    if isinstance(value, dict):
        parts = [f"{k if _BARE_KEY.match(str(k)) else json.dumps(str(k), ensure_ascii=False)}: {_inline(v)}" for k, v in value.items()]
        return "{ " + ", ".join(parts) + " }" if parts else "{}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_inline(v) for v in value) + "]"
    return _scalar(value)


def _pin_list(pins: list[dict[str, Any]]) -> str:
    if not pins:
        return "[]"
    return "[\n" + "\n".join(f"    {_inline(_declared_pin(p))}," for p in pins) + "\n  ]"


def _declared_pin(pin: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"id": pin["id"], "type": pin.get("type") or "any", "label": pin.get("label") or pin["id"]}
    for key in ("required", "description", "default"):
        if key in pin:
            out[key] = pin[key]
    return out


def _comment(lines: list[str]) -> str:
    out: list[str] = []
    for line in lines:
        for part in str(line).splitlines() or [""]:
            out.append(("// " + part).rstrip())
    return "\n".join(out)


def _declaration(kind: str, pins: dict[str, Any], *, tools: list[str] = (), builtins: list[str] = ()) -> str:
    return (
        "export const node = {\n"
        f"  kind: {_scalar(kind)},\n"
        f"  inputs: {_pin_list(pins.get('inputs') or [])},\n"
        f"  outputs: {_pin_list(pins.get('outputs') or [])},\n"
        "  settings: [],\n"
        f"  tools: {_inline(list(tools))},\n"
        f"  builtins: {_inline(list(builtins))},\n"
        "};"
    )


def _module(header: list[str], kind: str, pins: dict[str, Any], body: str, *, consts: str = "",
            tools: list[str] = (), builtins: list[str] = (), helpers: tuple[str, ...] = ()) -> str:
    wanted: list[str] = []
    for name in helpers:
        for need in (*_HELPER_NEEDS.get(name, ()), name):
            if need not in wanted:
                wanted.append(need)
    parts = ["// @ts-check", _comment(header), _declaration(kind, pins, tools=tools, builtins=builtins)]
    if consts:
        parts.append(consts)
    parts.append(f"{_RUN_DOC}\nexport default async function run(input, ducky) {{\n{body}\n}}")
    parts += [_HELPERS[name] for name in wanted]
    return "\n\n".join(parts) + "\n"


def _specs() -> dict[str, dict[str, Any]]:
    from backend.automations.catalog import node_specs

    return node_specs()


def _baked(cfg: dict[str, Any]) -> dict[str, Any]:
    """The settings written into the code: not the unwired pin values (inputs come in
    through ``input``) and not Spend (the Custom code node has its own switch)."""
    return {k: v for k, v in cfg.items() if k not in ("inputs", "spend")}


def _const(name: str, value: Any, doc: str) -> str:
    return f"/** {doc} */\nconst {name} = {js(value)};"


# --------------------------------------------------------------------------- real JS


def _tool_call(cfg: dict[str, Any], pins: dict[str, Any], header: list[str]) -> tuple[str, str] | None:
    name = str(cfg.get("name") or "").strip()
    raw = cfg.get("arguments")
    if raw is None and cfg.get("arguments_json"):
        try:
            raw = json.loads(str(cfg.get("arguments_json") or "{}"))
        except ValueError:
            return None
    if not name or not isinstance(raw, dict):
        return None  # the built-in reads the tool or its arguments from the run: keep it
    body = (
        f"  const args = await fill(ARGS, ducky);\n"
        f"  const data = await ducky.tool({_scalar(name)}, args);\n"
        f"  const result = {{ tool: {_scalar(name)}, output: data, data }};\n"
        "  return { result, text: pyJson(result) };"
    )
    consts = _const("ARGS", raw, "The tool's arguments; {{placeholders}} are filled from the run.")
    return _module(header, "step", pins, body, consts=consts, tools=[name], helpers=("fill", "pyJson")), "real"


def _input(ntype: str, cfg: dict[str, Any], pins: dict[str, Any], header: list[str]) -> tuple[str, str] | None:
    from backend.automations.runner import _input_value

    try:
        outputs = _input_value(ntype, cfg)
    except ValueError:
        return None  # the built-in reports what is wrong with the value when it runs
    consts = _const("VALUE", outputs, "What this node passes on (its value, set in the details).")
    return _module(header, "value", pins, "  return { ...VALUE };", consts=consts), "real"


def _template_text(cfg: dict[str, Any], pins: dict[str, Any], header: list[str]) -> tuple[str, str]:
    body = (
        "  const template = TEMPLATE.trim() ? TEMPLATE : Object.keys(input).map((name) => \"{{\" + name + \"}}\").join(\" \");\n"
        "  return { text: pyText(await ducky.template(template, input)) };"
    )
    consts = _const("TEMPLATE", str(cfg.get("template") or ""), "The template; blank joins every input.")
    return _module(header, "value", pins, body, consts=consts, helpers=("pyText",)), "real"


def _expression(cfg: dict[str, Any], pins: dict[str, Any], header: list[str]) -> tuple[str, str]:
    consts = _const("EXPRESSION", str(cfg.get("expression") or ""), "The expression (Workflow expression rules, not JavaScript).")
    return _module(header, "value", pins, "  return { result: await ducky.expr(EXPRESSION, input) };", consts=consts), "real"


def _list_make(cfg: dict[str, Any], pins: dict[str, Any], header: list[str]) -> tuple[str, str]:
    names = [str(name) for name in cfg.get("names") or []]
    body = (
        f"  const names = {_inline(names)};\n"
        "  const order = names.length ? names : Object.keys(input);\n"
        "  return { list: order.filter((name) => input[name] !== null && input[name] !== undefined).map((name) => input[name]) };"
    )
    return _module(header, "value", pins, body), "real"


def _list_get(cfg: dict[str, Any], pins: dict[str, Any], header: list[str]) -> tuple[str, str]:
    fallback = cfg.get("index", 0)
    body = (
        "  const items = asList(input.list);\n"
        f"  const raw = \"index\" in input ? input.index : {_scalar(fallback)};\n"
        "  const index = asNumber(raw !== null && raw !== undefined && raw !== \"\" ? raw : 0);\n"
        "  if (Number.isNaN(index) || !Number.isInteger(index)) throw new Error(`Index must be a whole number, not ${asText(raw)}.`);\n"
        "  if (!items.length) throw new Error(\"The list is empty.\");\n"
        "  if (index < -items.length || index >= items.length) {\n"
        "    throw new Error(`There is no item ${index}: the list has ${items.length} (first is 0, last is -1).`);\n"
        "  }\n"
        "  return { item: items[index < 0 ? items.length + index : index] };"
    )
    return _module(header, "value", pins, body, helpers=("asList", "asNumber", "asText")), "real"


def _list_count(_cfg: dict[str, Any], pins: dict[str, Any], header: list[str]) -> tuple[str, str]:
    body = (
        "  const value = input.list;\n"
        "  if (typeof value === \"string\" && !value.trim().startsWith(\"[\")) return { count: [...value].length };\n"
        "  if (value && typeof value === \"object\" && !Array.isArray(value) && !value.path) return { count: Object.keys(value).length };\n"
        "  return { count: asList(value).length };"
    )
    return _module(header, "value", pins, body, helpers=("asList",)), "real"


def _list_join(cfg: dict[str, Any], pins: dict[str, Any], header: list[str]) -> tuple[str, str]:
    body = (
        f"  const raw = \"separator\" in input ? input.separator : {_scalar(cfg.get('separator'))};\n"
        "  const separator = raw === null || raw === undefined ? \", \" : String(raw).replaceAll(\"\\\\n\", \"\\n\");\n"
        "  return { text: asList(input.list).map(asText).join(separator) };"
    )
    return _module(header, "value", pins, body, helpers=("asList", "asText")), "real"


_REAL = {
    "text.template": _template_text,
    "logic.expression": _expression,
    "list.make": _list_make,
    "list.get": _list_get,
    "list.count": _list_count,
    "list.join": _list_join,
}


# --------------------------------------------------------------------------- generate


def _flow(node: dict[str, Any], spec: dict[str, Any] | None, label: str, reason: str) -> dict[str, Any]:
    ntype = str(node.get("type") or "")
    cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
    lines = [f"{label} ({ntype}) chooses where the workflow goes, so it stays a built-in node.",
             _FLOW_DOCS.get(ntype) or str((spec or {}).get("description") or "It starts the workflow."), ""]
    if ntype == "logic.if":
        lines += [f"result = ({cfg.get('expression') or '…'})", 'if (result) follow the "true" wire', 'else follow the "false" wire']
    elif ntype == "flow.branch":
        lines += ["if (" + (f"the judge ducky says yes to: {cfg.get('prompt') or '…'}" if cfg.get("mode") == "agent"
                            else f"run.{cfg.get('field') or '…'} {cfg.get('op') or 'equals'} {cfg.get('equals') or cfg.get('contains') or ''}".rstrip()) + ")",
                  '  follow the "true" wire', 'else follow the "false" wire']
    elif ntype == "flow.foreach":
        lines += [f"for (const item of run.{cfg.get('field') or 'cards'}) follow the \"each\" wire", 'then follow the "done" wire']
    elif ntype == "flow.repeat":
        lines += [f"for (let attempt = 1; attempt <= {cfg.get('max') or 3}; attempt++) {{", '  follow the "each" wire',
                  f"  if ({cfg.get('expression') or 'until'}) break", "}", 'then follow the "done" wire with passed and attempt']
    elif ntype == "fortnite.servers":
        lines += ["up = Epic's status page says Login, Game Services and Matchmaking work",
                  'if (up) follow the "true" wire', 'else follow the "false" wire']
    elif ntype == "workflow.call":
        lines += [f"outputs = run workflow {cfg.get('workflow_id') or '…'} with this node's inputs", "if it has Return nodes and none is reached, stop this path"]
    return {"code": _comment(lines) + "\n", "kind": "flow", "convertible": False, "reason": reason}


def generate(node: dict[str, Any], specs: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """``{"code", "kind": "real" | "host" | "flow", "convertible", "reason"}`` for one node."""
    from backend.automations.pins import node_pins

    ntype = str(node.get("type") or "")
    cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
    specs = _specs() if specs is None else specs
    spec = specs.get(ntype)
    label = str(node.get("label") or (spec or {}).get("label") or ntype)
    if ntype == "code.js":
        from backend.automations.code_api import BLANK_CODE

        code = cfg.get("code") if isinstance(cfg.get("code"), str) and cfg.get("code").strip() else BLANK_CODE
        return {"code": code, "kind": "real", "convertible": False, "reason": "This node is custom code already."}
    if ntype in flow_types() or (spec or {}).get("role") == "starter":
        return _flow(node, spec, label, "It chooses where the workflow goes (or starts or ends it); custom code is one step and never picks a route.")
    if spec is None:
        return {"code": _comment([f"{label} ({ntype}) comes from a plugin that is off or not installed."]) + "\n",
                "kind": "flow", "convertible": False, "reason": "The plugin that adds this node is off or not installed."}
    pins = node_pins(node, spec)
    step = bool(pins["exec"])
    notes = ([_RESULT_NOTE] if step else []) + ([_TYPE_NOTES[ntype]] if ntype in _TYPE_NOTES else [])
    if spec.get("paid"):
        notes.append("It spends credits only when this node's Spend switch is on or a person presses play.")
    header = [f"{label}: the built-in {ntype} node as code.", *(str(spec.get("description") or "").splitlines()), *notes]
    made: tuple[str, str] | None = None
    if ntype == "tool.call":
        made = _tool_call(cfg, pins, header)
    elif ntype.startswith("input."):
        made = _input(ntype, cfg, pins, header)
    elif ntype in _REAL:
        made = _REAL[ntype](cfg, pins, header)
    if made is None and ntype == "tool.call":
        reason = ("Choose the tool and type its arguments first: custom code calls a tool it names (ducky.tool), "
                  "never one the run picks.")
        return {"code": _comment([f"{label}: the built-in {ntype} node.", reason]) + "\n", "kind": "host",
                "convertible": False, "reason": reason}
    if made is None:
        notes = [n for n in notes if n != _TYPE_NOTES.get("tool.call")]
        header = [f"{label}: runs the built-in {ntype} step (Python) with the settings below.",
                  *(str(spec.get("description") or "").splitlines()), *notes]
        consts = _const("CONFIG", _baked(cfg), "The settings this node had when it was converted.")
        body = f"  return await ducky.builtin({_scalar(ntype)}, input, CONFIG);"
        made = (_module(header, "step" if step else "value", pins, body, consts=consts, builtins=[ntype]), "host")
    return {"code": made[0], "kind": made[1], "convertible": True, "reason": " ".join(notes)}


EXAMPLES: list[dict[str, str]] = [
    {
        "title": "Run a terminal command (the command is written in the code, so once approved it runs without the pop-up)",
        "code": '''// @ts-check
export const node = {
  kind: "step",
  inputs: [{ id: "session", type: "text", label: "Terminal session" }],
  outputs: [{ id: "exit_code", type: "number", label: "Exit code" }, { id: "output", type: "text", label: "Output" }],
  settings: [],
  tools: ["ducky_terminal_run"],
  builtins: [],
};

/** @param {Record<string, any>} input @param {import("ducky").Ducky} ducky */
export default async function run(input, ducky) {
  const r = await ducky.tool("ducky_terminal_run", { session_id: input.session || "", command: "npm run build" });
  ducky.log("exit", r.exit_code);
  return { exit_code: r.exit_code, output: r.output_tail };
}
''',
    },
    {
        "title": "A value node: no white pins, runs when its value is needed",
        "code": '''// @ts-check
export const node = {
  kind: "value",
  inputs: [{ id: "names", type: "json", label: "Names", required: true }],
  outputs: [{ id: "text", type: "text", label: "Greeting" }, { id: "count", type: "number", label: "Count" }],
  settings: [{ id: "greeting", type: "text", label: "Greeting", default: "Hello" }],
  tools: [],
  builtins: [],
};

/** @param {Record<string, any>} input @param {import("ducky").Ducky} ducky */
export default async function run(input, ducky) {
  const names = Array.isArray(input.names) ? input.names : [];
  return { text: names.map((name) => `${ducky.settings.greeting} ${name}`).join("\\n"), count: names.length };
}
''',
    },
    {
        "title": "Run a built-in node from code (paid ones spend only when this node's Spend switch is on)",
        "code": '''// @ts-check
export const node = {
  kind: "step",
  inputs: [{ id: "image", type: "image", label: "Image", required: true }],
  outputs: [{ id: "image", type: "image", label: "Image" }],
  settings: [{ id: "width", type: "number", label: "Width", default: 512 }],
  tools: [],
  builtins: ["image.resize"],
};

/** @param {Record<string, any>} input @param {import("ducky").Ducky} ducky */
export default async function run(input, ducky) {
  const out = await ducky.builtin("image.resize", { image: input.image, width: ducky.settings.width }, { mode: "fit" });
  return { image: out.image };
}
''',
    },
]


# --------------------------------------------------------------------------- typed tool arguments


def _ts_type(schema: Any, depth: int = 0) -> str:
    if not isinstance(schema, dict) or depth > 4:
        return "any"
    if "enum" in schema and isinstance(schema["enum"], list):
        return " | ".join(json.dumps(v, ensure_ascii=False) for v in schema["enum"]) or "any"
    for key in ("anyOf", "oneOf"):
        if isinstance(schema.get(key), list):
            kinds = list(dict.fromkeys(_ts_type(s, depth + 1) for s in schema[key]))
            return " | ".join(kinds) or "any"
    kind = schema.get("type")
    if isinstance(kind, list):
        return " | ".join(dict.fromkeys(_ts_type({**schema, "type": k}, depth) for k in kind)) or "any"
    if kind == "string":
        return "string"
    if kind in ("number", "integer"):
        return "number"
    if kind == "boolean":
        return "boolean"
    if kind == "null":
        return "null"
    if kind == "array":
        inner = _ts_type(schema.get("items"), depth + 1)
        return f"Array<{inner}>"
    if kind == "object" or "properties" in schema:
        return _ts_object(schema, depth + 1)
    return "any"


def _ts_object(schema: dict[str, Any], depth: int) -> str:
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    if not props:
        return "Record<string, any>"
    required = set(schema.get("required") or [])
    rows = []
    for name, prop in props.items():
        if name in ("spend", "confirm_spend", "pretty"):
            continue
        key = name if _BARE_KEY.match(name) else json.dumps(name)
        rows.append(f"{key}{'' if name in required else '?'}: {_ts_type(prop, depth)}")
    return "{ " + "; ".join(rows) + " }" if rows else "Record<string, any>"


def tools_dts(names: list[str] | None) -> str:
    """``declare module "ducky" { export interface ToolArgs { ... } }`` for the named MCP
    tools: what ``ducky.tool(name, args)`` takes for each."""
    wanted = [str(n).strip() for n in names or [] if str(n).strip()][:40]
    if not wanted:
        return ""
    try:
        from backend.server import mcp

        manager = mcp._tool_manager
    except Exception:
        manager = None
    rows: list[str] = []
    for name in dict.fromkeys(wanted):
        tool = manager.get_tool(name) if manager is not None else None
        if tool is None:
            rows.append(f"    // {name}: no such tool here")
            continue
        doc = " ".join(str(getattr(tool, "description", "") or "").split())[:300]
        params = getattr(tool, "parameters", None) or {}
        if doc:
            rows.append(f"    /** {doc.replace('*/', '* /')} */")
        rows.append(f"    {json.dumps(name)}: {_ts_object(params, 1) if isinstance(params, dict) else 'Record<string, any>'};")
    return 'declare module "ducky" {\n  export interface ToolArgs {\n' + "\n".join(rows) + "\n  }\n}\n"
