"""Check a Custom code node's JavaScript without running it.

The ``node`` declaration (its pins, settings and the tools it may call) is read here
in Python as a plain literal, so a save never runs anyone's code. ``check`` also
refuses what the sandbox doesn't have (modules, network, eval) and asks the JS
engine for syntax errors when it is available. Problems carry 1-based line and
column numbers so the editor can underline them.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from backend.automations.pins import PIN_TYPES, clean_pins

MAX_NODE_BYTES = 64 * 1024
MAX_WORKFLOW_BYTES = 256 * 1024
KINDS = ("step", "value")
FIELD_TYPES = ("text", "textarea", "number", "boolean", "select", "model", "file", "folder", "project", "ducky")
_TOP_KEYS = ("kind", "inputs", "outputs", "settings", "tools", "builtins")
_PIN_KEYS = ("id", "label", "type", "required", "description", "default")
_FIELD_KEYS = ("id", "label", "type", "default", "options", "description")
_WORDISH = re.compile(r"^[A-Za-z_][\w\-]*$")
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_NUMBER = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_NODE_DECL = re.compile(r"\bexport const node\s*=")
_RUN_DECL = re.compile(r"\bexport default (?:async )?function run\s*\(")
_EXPORT = re.compile(r"\bexport\b")
# What the sandbox doesn't have, found in the code itself (strings and comments don't
# count, nor a property of the same name: input.process, x.fetch(), { import: 1 }).
_BANNED: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), message)
    for pattern, message in (
        (r"\bimport\b", "import isn't available: custom code can't load modules. Call tools with ducky.tool."),
        (r"\brequire\s*\(", "require() isn't available: custom code can't load modules. Call tools with ducky.tool."),
        (r"\bfetch\s*\(", "fetch() isn't available: custom code has no network. Call an MCP tool with ducky.tool."),
        (r"\bprocess\s*\.", "process isn't available in custom code."),
        (r"\beval\s*\(", "eval() is turned off in custom code."),
        (r"\bnew\s+Function\b", "new Function is turned off in custom code."),
        (r"\bFunction\s*\(", "Function() is turned off in custom code."),
        (r"\bWebAssembly\b", "WebAssembly isn't available in custom code."),
    )
)
_REGEX_AFTER_WORDS = frozenset({"return", "typeof", "case", "do", "else", "in", "of", "new", "delete", "void", "throw", "yield", "await", "instanceof"})

# What a node declaration may hold (workflow_code_api hands this to agents).
DECLARATION_SCHEMA: dict[str, Any] = {
    "kind": {"type": "string", "enum": list(KINDS), "default": "step",
             "doc": "step: white pins, runs in line. value: no white pins, runs when a value it makes is needed."},
    "inputs": {"type": "array", "items": {"id": "string", "type": list(PIN_TYPES), "label": "string?", "required": "boolean?", "description": "string?"}},
    "outputs": {"type": "array", "items": {"id": "string", "type": list(PIN_TYPES), "label": "string?", "description": "string?"}},
    "settings": {"type": "array", "items": {"id": "string", "type": list(FIELD_TYPES), "label": "string?", "default": "any?",
                                            "options": "for select: [\"a\", \"b\"] or [{id, label}]"}},
    "tools": {"type": "array", "items": "MCP tool name ducky.tool may call"},
    "builtins": {"type": "array", "items": "built-in node type ducky.builtin may run (never a route node, nor tool.call: use tools)"},
    "rules": [
        "node is a plain literal: objects, arrays, quoted strings, numbers, true/false/null, comments, trailing commas.",
        "No expressions, template literals, spreads or names in node; it is read without running the code.",
        "Exactly one `export const node = {...}` and one `export default async function run(input, ducky) {...}`; nothing else is exported.",
        "No import, require, fetch, process, eval, Function or WebAssembly.",
        f"At most {MAX_NODE_BYTES // 1024} KB of code per node and {MAX_WORKFLOW_BYTES // 1024} KB per workflow.",
    ],
}


def code_sha(code: str) -> str:
    return hashlib.sha256((code or "").encode("utf-8")).hexdigest()


def code_bytes(code: str) -> int:
    return len((code or "").encode("utf-8"))


# Route nodes (they start, end or choose where a workflow goes): code never runs one.
_FLOW_FALLBACK = frozenset({
    "start.manual", "start.cron", "start.chat", "spotlight.step", "spotlight.closed", "flow.input", "flow.output",
    "flow.end", "flow.foreach", "flow.repeat", "flow.branch", "logic.if", "workflow.call", "fortnite.servers",
    "pipeline.finish", "util.preview", "code.js",
})


def not_builtins() -> dict[str, str]:
    """Built-ins that aren't route nodes but still can't run through ducky.builtin, and why."""
    try:
        from backend.automations.code_api import NOT_BUILTINS

        return dict(NOT_BUILTINS)
    except ImportError:
        return {"tool.call": "tool.call can't run through ducky.builtin: call tools with ducky.tool and list each one in node.tools."}


def flow_types() -> frozenset[str]:
    try:
        from backend.automations.code_api import FLOW_TYPES

        return frozenset(FLOW_TYPES) | {"code.js"}
    except ImportError:
        return _FLOW_FALLBACK


class _Where:
    """1-based (line, column) of a character index."""

    def __init__(self, text: str):
        self.starts = [0] + [m.end() for m in re.finditer(r"\n", text)]

    def __call__(self, index: int) -> tuple[int, int]:
        lo, hi = 0, len(self.starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.starts[mid] <= index:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1, index - self.starts[lo] + 1


def _problem(where: _Where, index: int, message: str, severity: str = "error") -> dict[str, Any]:
    line, col = where(max(0, index))
    return {"line": line, "col": col, "message": message, "severity": severity}


# --------------------------------------------------------------------------- masking


def mask(code: str) -> str:
    """The code with comments, string and regex contents blanked (same length, same
    newlines), so a search for ``fetch(`` never trips on a log message. Code inside a
    template literal's ``${...}`` stays."""
    out = list(code)
    n = len(code)

    def blank(a: int, b: int) -> None:
        for k in range(max(0, a), min(b, n)):
            if out[k] not in "\r\n":
                out[k] = " "

    modes = ["code"]
    braces = [0]
    prev = ""  # the last code token, to tell a regex from a division
    i = 0
    while i < n:
        ch = code[i]
        if modes[-1] == "tpl":
            if ch == "\\":
                blank(i, i + 2)
                i += 2
            elif ch == "`":
                modes.pop()
                prev = "`"
                i += 1
            elif code.startswith("${", i):
                modes.append("code")
                braces.append(0)
                prev = "("
                i += 2
            else:
                blank(i, i + 1)
                i += 1
            continue
        if code.startswith("//", i):
            end = code.find("\n", i)
            end = n if end < 0 else end
            blank(i, end)
            i = end
            continue
        if code.startswith("/*", i):
            end = code.find("*/", i + 2)
            end = n if end < 0 else end + 2
            blank(i, end)
            i = end
            continue
        if ch in "'\"":
            j = i + 1
            while j < n and code[j] != ch and code[j] != "\n":
                j += 2 if code[j] == "\\" else 1
            blank(i + 1, j)
            prev = ch
            i = min(j + 1, n)
            continue
        if ch == "`":
            modes.append("tpl")
            i += 1
            continue
        if ch == "/" and (not prev or prev in "(,=:[!&|?{};+-*%<>~^" or prev in _REGEX_AFTER_WORDS):
            j, in_class = i + 1, False
            while j < n and code[j] != "\n":
                if code[j] == "\\":
                    j += 2
                    continue
                if code[j] == "[":
                    in_class = True
                elif code[j] == "]":
                    in_class = False
                elif code[j] == "/" and not in_class:
                    break
                j += 1
            blank(i + 1, j)
            i = min(j + 1, n)
            while i < n and (code[i].isalnum() or code[i] == "_"):
                i += 1
            prev = "/"
            continue
        word = _IDENT.match(code, i) if (ch.isalpha() or ch in "_$") else None
        if word:
            prev = word.group(0)
            i = word.end()
            continue
        if ch == "{":
            braces[-1] += 1
        elif ch == "}":
            if braces[-1] == 0 and len(modes) > 1:
                modes.pop()
                braces.pop()
                prev = "x"
                i += 1
                continue
            braces[-1] -= 1
        if not ch.isspace():
            prev = "x" if ch.isdigit() or ch in ")]" else ch
        i += 1
    return "".join(out)


# --------------------------------------------------------------------------- the node literal


class _LiteralError(ValueError):
    def __init__(self, message: str, index: int):
        super().__init__(message)
        self.index = index


class _Obj(dict):
    pos = 0
    keys_pos: dict[str, int]


class _Arr(list):
    pos = 0
    items_pos: list[int]


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0", "\\": "\\", "'": "'", '"': '"', "`": "`", "/": "/"}


class _Literal:
    """A JSON5-ish reader for ``export const node = {...}``: comments, bare or quoted
    keys, single or double quotes, trailing commas. Anything that would need running
    (a name, an expression, a template literal, a spread) is an error with its place."""

    def __init__(self, text: str, start: int):
        self.text = text
        self.i = start

    def fail(self, message: str, index: int | None = None) -> None:
        raise _LiteralError(message, self.i if index is None else index)

    def peek(self) -> str:
        return self.text[self.i] if self.i < len(self.text) else ""

    def ws(self) -> None:
        text = self.text
        while self.i < len(text):
            if text[self.i].isspace():
                self.i += 1
            elif text.startswith("//", self.i):
                end = text.find("\n", self.i)
                self.i = len(text) if end < 0 else end
            elif text.startswith("/*", self.i):
                end = text.find("*/", self.i + 2)
                if end < 0:
                    self.fail("This comment is never closed with */.")
                self.i = end + 2
            else:
                break

    def value(self) -> Any:
        self.ws()
        ch = self.peek()
        if not ch:
            self.fail("node ends too early: close every { and [.")
        if ch == "{":
            return self.obj()
        if ch == "[":
            return self.arr()
        if ch in "'\"":
            return self.string()
        if ch == "`":
            self.fail("Use plain quotes in node, not a template literal (`...`): node is read without running it.")
        if self.text.startswith("...", self.i):
            self.fail("node can't use ... (spread): write every value out.")
        if ch.isdigit() or ch in "+-.":
            return self.number()
        word = _IDENT.match(self.text, self.i)
        if word:
            name = word.group(0)
            if name in ("true", "false", "null"):
                self.i = word.end()
                return {"true": True, "false": False, "null": None}[name]
            self.fail(f"node can only hold plain values, and {name} is a name (node is read without running the code).")
        self.fail(f"Unexpected {ch!r} in node.")
        return None

    def number(self) -> Any:
        found = _NUMBER.match(self.text, self.i)
        if not found:
            self.fail("This isn't a number.")
        raw = found.group(0)
        self.i = found.end()
        follow = self.peek()
        if follow and (follow.isalnum() or follow in "_$"):
            self.fail("node holds plain decimal numbers only.")
        return float(raw) if any(c in raw for c in ".eE") else int(raw)

    def string(self) -> str:
        quote = self.text[self.i]
        start = self.i
        self.i += 1
        parts: list[str] = []
        text = self.text
        while True:
            if self.i >= len(text) or text[self.i] == "\n":
                self.fail("This string isn't closed on its line.", start)
            ch = text[self.i]
            if ch == quote:
                self.i += 1
                return "".join(parts)
            if ch != "\\":
                parts.append(ch)
                self.i += 1
                continue
            nxt = text[self.i + 1] if self.i + 1 < len(text) else ""
            if nxt == "\n":
                self.i += 2
            elif nxt == "\r" and text.startswith("\n", self.i + 2):
                self.i += 3
            elif nxt == "u":
                if text.startswith("{", self.i + 2):
                    end = text.find("}", self.i + 3)
                    digits = text[self.i + 3:end] if end > 0 else ""
                    self.i = end + 1 if end > 0 else self.i
                else:
                    digits = text[self.i + 2:self.i + 6]
                    self.i += 6
                try:
                    parts.append(chr(int(digits, 16)))
                except ValueError:
                    self.fail("Bad \\u escape in this string.")
            elif nxt == "x":
                try:
                    parts.append(chr(int(text[self.i + 2:self.i + 4], 16)))
                except ValueError:
                    self.fail("Bad \\x escape in this string.")
                self.i += 4
            else:
                parts.append(_ESCAPES.get(nxt, nxt))
                self.i += 2

    def key(self) -> tuple[str, int]:
        self.ws()
        at = self.i
        ch = self.peek()
        if ch in "'\"":
            return self.string(), at
        if ch == "[":
            self.fail("node can't use computed keys ([...]): write the key out.")
        if self.text.startswith("...", self.i):
            self.fail("node can't use ... (spread): write every value out.")
        word = _IDENT.match(self.text, self.i)
        if not word:
            self.fail("Expected a key here.")
        self.i = word.end()
        return word.group(0), at

    def obj(self) -> _Obj:
        out = _Obj()
        out.pos = self.i
        out.keys_pos = {}
        self.i += 1
        while True:
            self.ws()
            if self.peek() == "}":
                self.i += 1
                return out
            key, at = self.key()
            self.ws()
            if self.peek() != ":":
                self.fail(f"Expected : after {key}.")
            self.i += 1
            if key in out:
                self.fail(f"{key} appears twice.", at)
            out[key] = self.value()
            out.keys_pos[key] = at
            self.ws()
            if self.peek() == ",":
                self.i += 1
            elif self.peek() != "}":
                self.fail("Expected , or } here.")

    def arr(self) -> _Arr:
        out = _Arr()
        out.pos = self.i
        out.items_pos = []
        self.i += 1
        while True:
            self.ws()
            if self.peek() == "]":
                self.i += 1
                return out
            out.items_pos.append(self.i)
            out.append(self.value())
            self.ws()
            if self.peek() == ",":
                self.i += 1
            elif self.peek() != "]":
                self.fail("Expected , or ] here.")


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def parse_declaration(code: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """The ``node`` literal as plain data, or None and why not."""
    where = _Where(code)
    masked = mask(code)
    found = list(_NODE_DECL.finditer(masked))
    if not found:
        return None, [_problem(where, 0, "Declare the node: export const node = { kind, inputs, outputs, settings, tools, builtins };")]
    reader = _Literal(code, found[0].end())
    try:
        reader.ws()
        if reader.peek() != "{":
            reader.fail("node must be a plain object: export const node = { ... };")
        raw = reader.value()
        reader.ws()
        follow = reader.peek()
        if follow and follow in ".+-*/%?[(`&|=<>,":
            reader.fail("node must be a plain { ... } literal, with nothing after it but ;")
    except _LiteralError as exc:
        return None, [_problem(where, exc.index, str(exc))]
    return raw, []


def _declaration(raw: _Obj, where: _Where) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate the parsed literal: kind, pins, settings, tools, builtins."""
    problems: list[dict[str, Any]] = []

    def at(container: Any, key: str | int | None = None) -> int:
        if isinstance(container, _Obj) and isinstance(key, str):
            return container.keys_pos.get(key, container.pos)
        if isinstance(container, _Arr) and isinstance(key, int) and key < len(container.items_pos):
            return container.items_pos[key]
        return getattr(container, "pos", 0)

    def err(index: int, message: str, severity: str = "error") -> None:
        problems.append(_problem(where, index, message, severity))

    for key in raw:
        if key not in _TOP_KEYS:
            err(at(raw, key), f"node has no {key!r}; it takes {', '.join(_TOP_KEYS)}.", "warning")
    kind = raw.get("kind", "step")
    if kind not in KINDS:
        err(at(raw, "kind"), 'kind is "step" (white pins, runs in line) or "value" (runs when its value is needed).')
        kind = "step"

    def rows(key: str) -> list[tuple[int, Any]]:
        value = raw.get(key, [])
        if not isinstance(value, list):
            err(at(raw, key), f"{key} must be a list [...].")
            return []
        return [(at(value, n), item) for n, item in enumerate(value)]

    def pins(key: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for index, row in rows(key):
            if not isinstance(row, dict):
                err(index, f"Each of {key} is an object like {{ id: \"text\", type: \"text\", label: \"Text\" }}.")
                continue
            pid = row.get("id")
            if not isinstance(pid, str) or not pid.strip():
                err(at(row, "id") if "id" in row else index, f"Each of {key} needs an id.")
                continue
            if any(p["id"] == pid for p in out):
                err(at(row, "id"), f"{key} has {pid!r} twice.")
                continue
            if not _WORDISH.match(pid):
                err(at(row, "id"), f"{pid!r} can't be used in {{{{nodes.<id>.<pin>}}}} placeholders; use letters, digits, _ and -.", "warning")
            if row.get("type") not in PIN_TYPES:
                err(at(row, "type") if "type" in row else index, f"{pid}: type is one of {', '.join(PIN_TYPES)}.")
            if "label" in row and not isinstance(row["label"], str):
                err(at(row, "label"), f"{pid}: label is text.")
            if "required" in row and not isinstance(row["required"], bool):
                err(at(row, "required"), f"{pid}: required is true or false.")
            for extra in row:
                if extra not in _PIN_KEYS:
                    err(at(row, extra), f"{pid}: pins take {', '.join(_PIN_KEYS)}, not {extra!r}.", "warning")
            out.append(_plain(row))
        return out

    def fields() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for index, row in rows("settings"):
            if not isinstance(row, dict):
                err(index, 'Each setting is an object like { id: "timeout", type: "number", label: "Timeout (s)" }.')
                continue
            fid = row.get("id")
            if not isinstance(fid, str) or not fid.strip():
                err(at(row, "id") if "id" in row else index, "Each setting needs an id.")
                continue
            if any(f["id"] == fid for f in out):
                err(at(row, "id"), f"settings has {fid!r} twice.")
                continue
            ftype = row.get("type")
            if ftype not in FIELD_TYPES:
                err(at(row, "type") if "type" in row else index, f"{fid}: type is one of {', '.join(FIELD_TYPES)}.")
                ftype = "text"
            field: dict[str, Any] = {"id": fid, "label": str(row.get("label") or fid), "type": ftype}
            if "default" in row:
                field["default"] = _plain(row["default"])
            if row.get("description"):
                field["description"] = str(row["description"])
            options = row.get("options")
            if options is not None:
                clean: list[dict[str, str]] = []
                for opt in options if isinstance(options, list) else []:
                    if isinstance(opt, str) and opt.strip():
                        clean.append({"id": opt, "label": opt})
                    elif isinstance(opt, dict) and (opt.get("id") or opt.get("value")):
                        oid = str(opt.get("id") or opt.get("value"))
                        clean.append({"id": oid, "label": str(opt.get("label") or oid)})
                if not isinstance(options, list) or len(clean) != len(options):
                    err(at(row, "options"), f'{fid}: options are ["a", "b"] or [{{ id: "a", label: "A" }}].')
                field["options"] = clean
            if ftype == "select" and not field.get("options"):
                err(at(row, "type"), f"{fid}: a select needs options.", "warning")
            for extra in row:
                if extra not in _FIELD_KEYS:
                    err(at(row, extra), f"{fid}: settings take {', '.join(_FIELD_KEYS)}, not {extra!r}.", "warning")
            out.append(field)
        return out

    def names(key: str) -> list[str]:
        out: list[str] = []
        for index, item in rows(key):
            if not isinstance(item, str) or not item.strip():
                err(index, f"Each of {key} is a name in quotes.")
            elif item not in out:
                out.append(item)
        return out

    inputs, outputs = pins("inputs"), pins("outputs")
    settings = fields()
    tools, builtins = names("tools"), names("builtins")
    flow, refused = flow_types(), not_builtins()
    for n, name in enumerate(builtins):
        index = at(raw.get("builtins"), n) if isinstance(raw.get("builtins"), _Arr) else at(raw, "builtins")
        if name in flow:
            err(index, f"{name} chooses where a workflow goes (or starts or ends one), so code can't run it.")
        elif name in refused:
            err(index, refused[name])
    decl = {"kind": kind, "inputs": inputs, "outputs": outputs, "settings": settings, "tools": tools, "builtins": builtins}
    return decl, problems


# --------------------------------------------------------------------------- check


def _syntax(code: str) -> list[dict[str, Any]]:
    try:
        from backend.automations import jsrt
    except ImportError:
        return []
    try:
        found = jsrt.check_syntax(code)
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for row in found or []:
        if isinstance(row, dict) and row.get("message"):
            out.append({
                "line": max(1, int(row.get("line") or 1)),
                "col": max(1, int(row.get("col") or 1)),
                "message": str(row["message"]),
                "severity": "warning" if row.get("severity") == "warning" else "error",
            })
    return out


def _is_property(masked: str, start: int) -> bool:
    """A property named like a banned global: after a dot (x.fetch, x?.fetch, not
    ...fetch) or an object key (import: 1)."""
    before = masked[:start].rstrip()
    if before.endswith(".") and not before.endswith("..."):
        return True
    word = _IDENT.match(masked, start)
    return bool(word) and masked[word.end():].lstrip().startswith(":") and not before.endswith("?")


def check(code: str) -> dict[str, Any]:
    """Everything wrong with one node's code, plus what its declaration says
    (pins, settings, the tools and built-ins it may use)."""
    code = code if isinstance(code, str) else ""
    where = _Where(code)
    problems: list[dict[str, Any]] = []
    size = code_bytes(code)
    if size > MAX_NODE_BYTES:
        problems.append(_problem(where, 0, f"This code is {size // 1024} KB; a node holds at most {MAX_NODE_BYTES // 1024} KB."))
    masked = mask(code)
    for pattern, message in _BANNED:
        for found in pattern.finditer(masked):
            if pattern.pattern.startswith(r"\bFunction") and re.search(r"\bnew\s+$", masked[:found.start()]):
                continue  # already reported as new Function
            if _is_property(masked, found.start()):
                continue
            problems.append(_problem(where, found.start(), message))
    decls = list(_NODE_DECL.finditer(masked))
    runs = list(_RUN_DECL.finditer(masked))
    for extra in decls[1:]:
        problems.append(_problem(where, extra.start(), "node is declared twice; keep one export const node."))
    if not runs:
        problems.append(_problem(where, 0, "Add the run function: export default async function run(input, ducky) { ... }"))
    for extra in runs[1:]:
        problems.append(_problem(where, extra.start(), "run is declared twice; keep one export default function run."))
    allowed = {m.start() for m in decls[:1] + runs[:1]}
    for found in _EXPORT.finditer(masked):
        if found.start() not in allowed and not any(found.start() == m.start() for m in decls[1:] + runs[1:]):
            problems.append(_problem(where, found.start(), "Only node and run are exported; remove this export."))
    raw, decl_problems = parse_declaration(code)
    problems += decl_problems
    decl: dict[str, Any] | None = None
    if isinstance(raw, _Obj):
        decl, more = _declaration(raw, where)
        problems += more
    if size <= MAX_NODE_BYTES:
        problems += _syntax(code)
    seen: set[tuple[int, int, str]] = set()
    unique: list[dict[str, Any]] = []
    for row in sorted(problems, key=lambda p: (p["line"], p["col"])):
        key = (row["line"], row["col"], row["message"])
        if key not in seen:
            seen.add(key)
            unique.append(row)
    ok = not any(p["severity"] == "error" for p in unique)
    pins = {"exec": (decl or {}).get("kind", "step") == "step",
            "inputs": clean_pins((decl or {}).get("inputs")), "outputs": clean_pins((decl or {}).get("outputs"))}
    return {
        "ok": ok,
        "problems": unique,
        "node": decl,
        "pins": pins,
        "settings_spec": list((decl or {}).get("settings") or []),
        "uses": {"tools": list((decl or {}).get("tools") or []), "builtins": list((decl or {}).get("builtins") or [])},
        "code_sha": code_sha(code),
    }


def first_error(problems: list[dict[str, Any]]) -> str:
    """"Line N: message" of the first error, for a step that won't run."""
    row = next((p for p in problems or [] if p.get("severity") == "error"), None)
    return f"Line {row['line']}: {row['message']}" if row else ""
