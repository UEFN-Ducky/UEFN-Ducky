"""A small, safe, JS-like expression language for If / Expression / Compare nodes.

    score > 10 && name.includes("duck")
    items.length ? items[0] : "none"
    round(price * 1.2)

No Python ``eval``: a Pratt parser builds a tree and a step-limited walker evaluates it
against the node's inputs and the run's fields. Unknown names are ``null``.
"""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from typing import Any

MAX_SOURCE = 2000
MAX_STEPS = 10_000
MAX_TEXT = 100_000


class ExprError(ValueError):
    """A syntax or evaluation error, with where it happened."""


_TOKEN = re.compile(
    r"""\s*(?:
      (?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\.\d+)
     |(?P<str>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
     |(?P<name>[A-Za-z_$][\w$]*)
     |(?P<op>===|!==|==|!=|<=|>=|&&|\|\||[-+*/%<>!?:.,()\[\]])
    )""",
    re.VERBOSE,
)


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", "'": "'", '"': '"'}


def _unquote(raw: str) -> str:
    """'text' or "text" with \\n, \\t, \\uXXXX and quote escapes."""
    body, out, i = raw[1:-1], [], 0
    while i < len(body):
        char = body[i]
        if char == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt == "u" and re.fullmatch(r"[0-9a-fA-F]{4}", body[i + 2:i + 6] or ""):
                out.append(chr(int(body[i + 2:i + 6], 16)))
                i += 6
                continue
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        out.append(char)
        i += 1
    return "".join(out)


def _tokens(src: str) -> list[tuple[str, Any, int]]:
    out: list[tuple[str, Any, int]] = []
    at = 0
    while at < len(src):
        if src[at:].strip() == "":
            break
        m = _TOKEN.match(src, at)
        if not m or m.end() == at:
            raise ExprError(f"Unexpected {src[at:].strip()[:1]!r} at {at + 1}")
        kind = m.lastgroup or ""
        raw = m.group(kind)
        if kind == "num":
            out.append(("num", float(raw) if any(c in raw for c in ".eE") else int(raw), m.start(kind)))
        elif kind == "str":
            out.append(("str", _unquote(raw), m.start(kind)))
        else:
            out.append((kind, raw, m.start(kind)))
        at = m.end()
    out.append(("end", None, len(src)))
    return out


_BINARY = {
    "||": 1, "&&": 2,
    "==": 3, "!=": 3, "===": 3, "!==": 3,
    "<": 4, "<=": 4, ">": 4, ">=": 4,
    "+": 5, "-": 5,
    "*": 6, "/": 6, "%": 6,
}
_CONSTANTS = {"true": True, "false": False, "null": None, "undefined": None}


class _Parser:
    def __init__(self, src: str):
        self.toks = _tokens(src)
        self.i = 0

    def peek(self) -> tuple[str, Any, int]:
        return self.toks[self.i]

    def take(self, value: str | None = None) -> tuple[str, Any, int]:
        tok = self.toks[self.i]
        if value is not None and tok[1] != value:
            got = "the end" if tok[0] == "end" else repr(tok[1])
            raise ExprError(f"Expected {value!r} at {tok[2] + 1}, got {got}")
        self.i += 1
        return tok

    def parse(self) -> tuple:
        node = self.ternary()
        if self.peek()[0] != "end":
            tok = self.peek()
            raise ExprError(f"Unexpected {tok[1]!r} at {tok[2] + 1}")
        return node

    def ternary(self) -> tuple:
        cond = self.binary(1)
        if self.peek()[1] == "?":
            self.take("?")
            yes = self.ternary()
            self.take(":")
            no = self.ternary()
            return ("?", cond, yes, no)
        return cond

    def binary(self, level: int) -> tuple:
        left = self.unary()
        while True:
            kind, op, _ = self.peek()
            if kind != "op" or op not in _BINARY or _BINARY[op] < level:
                return left
            self.take()
            right = self.binary(_BINARY[op] + 1)
            left = ("bin", op, left, right)

    def unary(self) -> tuple:
        kind, op, _ = self.peek()
        if kind == "op" and op in ("!", "-", "+"):
            self.take()
            return ("un", op, self.unary())
        return self.postfix()

    def postfix(self) -> tuple:
        node = self.primary()
        while True:
            _, op, at = self.peek()
            if op == ".":
                self.take()
                kind, name, where = self.take()
                if kind != "name":
                    raise ExprError(f"Expected a name after '.' at {where + 1}")
                node = ("get", node, ("lit", name))
            elif op == "[":
                self.take()
                key = self.ternary()
                self.take("]")
                node = ("get", node, key)
            elif op == "(":
                self.take()
                args: list[tuple] = []
                if self.peek()[1] != ")":
                    args.append(self.ternary())
                    while self.peek()[1] == ",":
                        self.take()
                        args.append(self.ternary())
                self.take(")")
                node = ("call", node, args, at)
            else:
                return node

    def primary(self) -> tuple:
        kind, value, at = self.take()
        if kind in ("num", "str"):
            return ("lit", value)
        if kind == "name":
            return ("lit", _CONSTANTS[value]) if value in _CONSTANTS else ("name", value)
        if value == "(":
            node = self.ternary()
            self.take(")")
            return node
        if value == "[":
            items: list[tuple] = []
            if self.peek()[1] != "]":
                items.append(self.ternary())
                while self.peek()[1] == ",":
                    self.take()
                    items.append(self.ternary())
            self.take("]")
            return ("list", items)
        raise ExprError("The expression ends too early" if kind == "end" else f"Unexpected {value!r} at {at + 1}")


@lru_cache(maxsize=512)
def compile_expr(src: str) -> tuple:
    text = (src or "").strip()
    if not text:
        raise ExprError("Write an expression, e.g. score > 10")
    if len(text) > MAX_SOURCE:
        raise ExprError(f"Keep expressions under {MAX_SOURCE} characters")
    return _Parser(text).parse()


def check(src: str) -> str:
    """'' when the expression parses, else what is wrong."""
    try:
        compile_expr(src)
        return ""
    except ExprError as exc:
        return str(exc)


def truthy(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0 and not (isinstance(value, float) and math.isnan(value))
    if isinstance(value, str):
        return value != ""
    return True


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def as_number(value: Any) -> float | int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    if value is None:
        return 0
    try:
        text = str(value).strip()
        number = float(text)
        return int(number) if number.is_integer() and "." not in text and "e" not in text.lower() else number
    except (TypeError, ValueError):
        return math.nan


def _loose_eq(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (int, float)) and isinstance(b, str) or isinstance(b, (int, float)) and isinstance(a, str):
        return as_number(a) == as_number(b)
    return a == b


def _strict_eq(a: Any, b: Any) -> bool:
    num = (int, float)
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, num) and isinstance(b, num):
        return a == b
    return type(a) is type(b) and a == b


def _length(value: Any) -> int | None:
    return len(value) if isinstance(value, (str, list, dict)) else None


def _method(target: Any, name: str, args: list[Any]) -> Any:
    if isinstance(target, str):
        text = target
        if name == "includes":
            return as_text(args[0] if args else "") in text
        if name == "startsWith":
            return text.startswith(as_text(args[0] if args else ""))
        if name == "endsWith":
            return text.endswith(as_text(args[0] if args else ""))
        if name == "toLowerCase":
            return text.lower()
        if name == "toUpperCase":
            return text.upper()
        if name == "trim":
            return text.strip()
        if name == "split":
            return text.split(as_text(args[0])) if args and as_text(args[0]) else list(text)
        if name == "slice":
            return text[_slice(args, len(text))]
        if name == "replace":
            return text.replace(as_text(args[0] if args else ""), as_text(args[1] if len(args) > 1 else ""), 1)
        if name == "indexOf":
            return text.find(as_text(args[0] if args else ""))
    if isinstance(target, list):
        if name == "includes":
            return any(_strict_eq(item, args[0] if args else None) for item in target)
        if name == "join":
            return (as_text(args[0]) if args else ",").join(as_text(item) for item in target)
        if name == "slice":
            return target[_slice(args, len(target))]
        if name == "indexOf":
            return next((i for i, item in enumerate(target) if _strict_eq(item, args[0] if args else None)), -1)
    if isinstance(target, dict) and name == "keys":
        return list(target)
    raise ExprError(f"{name}() doesn't work on {_kind(target)}")


def _slice(args: list[Any], size: int) -> slice:
    start = int(as_number(args[0])) if args else 0
    end = int(as_number(args[1])) if len(args) > 1 and args[1] is not None else size
    return slice(start, end)


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true/false"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, list):
        return "a list"
    return "an object"


def _matches(text: Any, pattern: Any) -> bool:
    try:
        return re.search(as_text(pattern), as_text(text)) is not None
    except re.error as exc:
        raise ExprError(f"matches(): {exc}") from exc


def _fail(message: Any = "Workflow validation failed") -> None:
    """Stop an expression-driven workflow with an actionable validation error."""
    raise ExprError(as_text(message))


_FUNCTIONS: dict[str, Any] = {
    "fail": _fail,
    "len": lambda v: _length(v) or 0,
    "number": as_number,
    "text": as_text,
    "bool": truthy,
    "round": lambda v, digits=0: round(as_number(v), int(as_number(digits))) if digits else round(as_number(v)),
    "floor": lambda v: math.floor(as_number(v)),
    "ceil": lambda v: math.ceil(as_number(v)),
    "abs": lambda v: abs(as_number(v)),
    "min": lambda *v: min(as_number(x) for x in (v[0] if len(v) == 1 and isinstance(v[0], list) else v)),
    "max": lambda *v: max(as_number(x) for x in (v[0] if len(v) == 1 and isinstance(v[0], list) else v)),
    "contains": lambda haystack, needle: (as_text(needle) in haystack) if isinstance(haystack, str)
    else any(_strict_eq(item, needle) for item in haystack) if isinstance(haystack, list)
    else (as_text(needle) in haystack) if isinstance(haystack, dict) else False,
    "matches": _matches,
    "json": lambda v: json.loads(v) if isinstance(v, str) else v,
    "lower": lambda v: as_text(v).lower(),
    "upper": lambda v: as_text(v).upper(),
}


class _Walker:
    def __init__(self, scope: dict[str, Any]):
        self.scope = scope
        self.steps = 0

    def run(self, node: tuple) -> Any:
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise ExprError("The expression took too many steps")
        kind = node[0]
        if kind == "lit":
            return node[1]
        if kind == "name":
            return _lookup(self.scope, node[1])
        if kind == "list":
            return [self.run(item) for item in node[1]]
        if kind == "?":
            return self.run(node[2]) if truthy(self.run(node[1])) else self.run(node[3])
        if kind == "un":
            value = self.run(node[2])
            if node[1] == "!":
                return not truthy(value)
            number = as_number(value)
            return -number if node[1] == "-" else number
        if kind == "bin":
            return self.binary(node[1], node[2], node[3])
        if kind == "get":
            return _get(self.run(node[1]), self.run(node[2]))
        if kind == "call":
            return self.call(node[1], node[2])
        raise ExprError("Unknown expression")

    def binary(self, op: str, left_node: tuple, right_node: tuple) -> Any:
        left = self.run(left_node)
        if op == "&&":
            return self.run(right_node) if truthy(left) else left
        if op == "||":
            return left if truthy(left) else self.run(right_node)
        right = self.run(right_node)
        if op == "+":
            if isinstance(left, list) and isinstance(right, list):
                return left + right
            if isinstance(left, str) or isinstance(right, str) or isinstance(left, (dict, list)) or isinstance(right, (dict, list)):
                out = as_text(left) + as_text(right)
                if len(out) > MAX_TEXT:
                    raise ExprError("That text got too long")
                return out
            return as_number(left) + as_number(right)
        if op in ("-", "*", "/", "%"):
            a, b = as_number(left), as_number(right)
            if op == "-":
                return a - b
            if op == "*":
                return a * b
            if b == 0:
                return math.nan if a == 0 else math.copysign(math.inf, a)
            return a / b if op == "/" else math.fmod(a, b)
        if op == "==":
            return _loose_eq(left, right)
        if op == "!=":
            return not _loose_eq(left, right)
        if op == "===":
            return _strict_eq(left, right)
        if op == "!==":
            return not _strict_eq(left, right)
        if isinstance(left, str) and isinstance(right, str):
            a, b = left, right
        else:
            a, b = as_number(left), as_number(right)
        try:
            return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
        except TypeError as exc:
            raise ExprError(f"Can't compare {_kind(left)} with {_kind(right)}") from exc

    def call(self, callee: tuple, arg_nodes: list[tuple]) -> Any:
        args = [self.run(arg) for arg in arg_nodes]
        if callee[0] == "get" and callee[2][0] == "lit" and isinstance(callee[2][1], str):
            return _method(self.run(callee[1]), callee[2][1], args)
        if callee[0] == "name" and callee[1] in _FUNCTIONS:
            try:
                return _FUNCTIONS[callee[1]](*args)
            except ExprError:
                raise
            except (TypeError, ValueError) as exc:
                raise ExprError(f"{callee[1]}(): {exc}") from exc
        name = callee[1] if callee[0] == "name" else "that"
        raise ExprError(f"{name} isn't a function you can call here")


def _lookup(scope: dict[str, Any], name: str) -> Any:
    if name in scope:
        return scope[name]
    # Dotted run fields ("player.name") also reachable as one name.
    return None


def _get(target: Any, key: Any) -> Any:
    if key == "length":
        size = _length(target)
        if size is not None:
            return size
    if isinstance(target, dict):
        return target.get(as_text(key) if not isinstance(key, str) else key)
    if isinstance(target, (list, str)):
        number = as_number(key)
        if isinstance(number, (int, float)) and not math.isnan(number) and float(number).is_integer():
            index = int(number)
            if -len(target) <= index < len(target):
                return target[index]
        return None
    return None


def evaluate(src: str, scope: dict[str, Any] | None = None) -> Any:
    """Evaluate ``src`` against ``scope`` (node inputs first, then run fields)."""
    return _Walker(dict(scope or {})).run(compile_expr(src))
