"""Findings-only heuristic linter for Verse sources (no UEFN, no LSP, no deps).

Targets the compile-error classes the offline verse-lsp scan cannot see:
effect specifiers (no_rollback in failure contexts), reserved bindings,
missing ``using`` lines, field initialisers that call functions, and a
handful of known non-existent APIs.

Line/regex heuristics only. False negatives are acceptable; false positives
are kept low. ``lint_verse`` never raises on odd input and never blocks a
write — callers attach the findings to their result so the agent can fix
them before compiling.

Finding shape::

    {"line": int, "rule": str, "severity": "error" | "warning",
     "message": str, "fix": str}
"""

from __future__ import annotations

import re
from typing import Any

EFFECT_SPECIFIERS = frozenset(
    {"transacts", "computes", "decides", "converges", "reads", "varies", "suspends"}
)

SHADOW_BUILTINS = frozenset(
    {
        "Distance",
        "DistanceSquared",
        "Dot",
        "Cross",
        "Normalize",
        "Lerp",
        "Sqrt",
        "Sin",
        "Cos",
        "Tan",
        "Floor",
        "Ceil",
        "Round",
        "Int",
        "Min",
        "Max",
        "Clamp",
        "Print",
        "Sleep",
        "Exp",
        "Log",
        "Pow",
        "Abs",
    }
)

_KEYWORDS = frozenset(
    {
        "if",
        "for",
        "case",
        "loop",
        "else",
        "then",
        "do",
        "block",
        "spawn",
        "branch",
        "sync",
        "race",
        "rush",
        "defer",
        "return",
        "not",
        "and",
        "or",
        "set",
        "var",
        "using",
        "module",
        "class",
        "struct",
        "enum",
        "interface",
        "external",
        "array",
        "map",
        "option",
        "tuple",
        "logic",
    }
)

# Name<spec>*(params)<spec>*:type =   — one-line function signature.
_SIG_RE = re.compile(r"^(\s*)([A-Za-z_]\w*)((?:<\w+>)*)\((.*)\)((?:<\w+>)*)\s*:\s*([^=]+?)\s*=(.*)$")
_CLASS_RE = re.compile(r":=\s*(?:<\w+>\s*)*class\b")
_SIG_START_RE = re.compile(r"^\s*([A-Za-z_]\w*)(?:<\w+>)*\(")
_FIELD_CALL_RE = re.compile(
    r"^\s*(?:var\s+)?[A-Za-z_]\w*(?:<\w+>)*\s*:\s*[^=]+=\s*([A-Z]\w*(?:\.\w+)*)\("
)
_USING_RE = re.compile(r"^\s*using\s*\{\s*([^}]*?)\s*\}")
_ARRAY_BLOCK_RE = re.compile(r"\barray\s*:\s*$")
_ARCHETYPE_HEADER_RE = re.compile(r"^\s*[A-Za-z_]\w*\s*:\s*$")
_IF_HEAD_RE = re.compile(r"\bif\s*:\s*$")
_THEN_ELSE_RE = re.compile(r"^\s*(?:then|else)\s*:")
_SET_RE = re.compile(r"\bset\s+[A-Za-z_]")
_INT_DIV_RE = re.compile(r":=\s*([A-Za-z_]\w*|\d+)\s*/\s*([A-Za-z_]\w*|\d+)\s*$")
_UNDERSCORE_RES = (
    re.compile(r"\bfor\s*\(\s*_\s*:"),
    re.compile(r"(?<![\w.])_\s*:="),
    re.compile(r"\(\s*_\s*,"),
    re.compile(r",\s*_\s*\)"),
)
_SHADOW_RE = re.compile(
    r"(?<![\w.@?])(?:var\s+)?(" + "|".join(sorted(SHADOW_BUILTINS)) + r")\s*:"
)
_C_COMMENT_RE = re.compile(r"//|/\*")
_TOSTRING_RE = re.compile(r"\bToString\(\s*([A-Za-z_]\w*)\s*\)")

# (token regex, substring that must appear in some `using` path, human path)
_USING_RULES: tuple[tuple[str, str, str], ...] = (
    (r"\bcollision_point\b", "/Verse.org/SceneGraph", "/Verse.org/SceneGraph"),
    (r"\bFindSweepHits\b", "/Verse.org/SceneGraph", "/Verse.org/SceneGraph"),
    (r"\bmesh_component\b", "/Verse.org/SceneGraph", "/Verse.org/SceneGraph"),
    (r"\bentity\s*\{", "/Verse.org/SceneGraph", "/Verse.org/SceneGraph"),
    (r"\bGetSimulationEntity\b", "/Verse.org/SceneGraph", "/Verse.org/SceneGraph"),
    # Bare GetPlayspace() is a creative_device method and needs no import; only the
    # fort_playspace type / GetPlayspaceForEntity come from the Playspaces module.
    (r"\bfort_playspace\b", "/Fortnite.com/Playspaces", "/Fortnite.com/Playspaces"),
    (r"\bGetPlayspaceForEntity\b", "/Fortnite.com/Playspaces", "/Fortnite.com/Playspaces"),
    (r"\bfort_character\b", "/Fortnite.com/Characters", "/Fortnite.com/Characters"),
    (r"\bGetFortCharacter\b", "/Fortnite.com/Characters", "/Fortnite.com/Characters"),
    (r"\bcreative_device\b", "/Fortnite.com/Devices", "/Fortnite.com/Devices"),
    (r"\bvector3\s*\{", "SpatialMath", "/Verse.org/SpatialMath"),
    (r"\btransform\s*\{", "SpatialMath", "/Verse.org/SpatialMath"),
    (r"\bGetPlayerInput\b", "/Verse.org/Input", "/Verse.org/Input"),
    (r"\binput_action\b", "/Verse.org/Input", "/Verse.org/Input"),
    (r"\bTouchMapping\b", "/Verse.org/Input/UI", "/Verse.org/Input/UI"),
    (r"\bPointerSelect\b", "/Verse.org/Input/UI", "/Verse.org/Input/UI"),
    (r"\bPointerZoom\b", "/Verse.org/Input/UI", "/Verse.org/Input/UI"),
    (r"\banimation_sequence\b", "/Verse.org/Assets", "/Verse.org/Assets"),
    (r"\bcolor\s*\{", "/Verse.org/Colors", "/Verse.org/Colors"),
)

# (regex, message, fix, severity)
_BAD_API_RULES: tuple[tuple[str, str, str, str], ...] = (
    (
        r"\w*Timer\w*\.Reset\(\s*\)",
        "`timer_device.Reset()` with no agent does not exist.",
        "Use `ResetForAll()` (or `Reset(Agent)` for one agent).",
        "error",
    ),
    (
        r"\bMoveToLocation\(",
        "`MoveToLocation` does not exist.",
        "Use `MoveTo[]` on the navigatable from `fort_character`, or a Verse Scene Graph movement component.",
        "error",
    ),
    (
        r"\bLog10\(",
        "`Log10` does not exist in Verse.",
        "Use `Log(X, ?Base := 10.0)`.",
        "error",
    ),
)

# Tokens that need `using { /Fortnite.com/UI }` (the Temporary/UI import is not enough).
_UI_TOKEN_RE = re.compile(r"\b(text_block|canvas|stack_box|color_block|texture_block)\b")
_FORTNITE_UI_USING = "/Fortnite.com/UI"

_IF_ELSE_HEAD_RE = re.compile(r"^\s*(?:if\s*\(.*\)|else\s+if\s*\(.*\)|else)\s*:\s*$")
_LONE_BRACES_RE = re.compile(r"^\s*\{\s*\}\s*$")
_MODULE_DECL_RE = re.compile(r":=\s*(?:<\w+>\s*)*module\b")


class _Func:
    __slots__ = ("name", "line", "indent", "effects")

    def __init__(self, name: str, line: int, indent: int, effects: set[str]) -> None:
        self.name = name
        self.line = line
        self.indent = indent
        self.effects = effects


def _indent(line: str) -> int:
    expanded = line.expandtabs(4)
    return len(expanded) - len(expanded.lstrip(" "))


def _strip_comments_and_strings(lines: list[str]) -> list[str]:
    """Blank string contents and remove comments, preserving line count/columns."""
    out: list[str] = []
    in_block = False
    for line in lines:
        buf: list[str] = []
        i = 0
        n = len(line)
        while i < n:
            if in_block:
                if line.startswith("#>", i):
                    in_block = False
                    buf.append("  ")
                    i += 2
                else:
                    buf.append(" ")
                    i += 1
                continue
            if line.startswith("<#", i):
                in_block = True
                buf.append("  ")
                i += 2
                continue
            ch = line[i]
            if ch == "#":
                buf.append(" " * (n - i))
                break
            if ch == '"':
                buf.append('"')
                i += 1
                while i < n:
                    c = line[i]
                    if c == "\\" and i + 1 < n:
                        buf.append("  ")
                        i += 2
                        continue
                    if c == '"':
                        buf.append('"')
                        i += 1
                        break
                    buf.append(" ")
                    i += 1
                continue
            buf.append(ch)
            i += 1
        out.append("".join(buf))
    return out


def _match_close(line: str, start: int, open_ch: str, close_ch: str) -> int:
    """Index of the bracket closing ``line[start]`` (or len(line) when unbalanced)."""
    depth = 0
    for i in range(start, len(line)):
        c = line[i]
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i
    return len(line)


def _top_level_commas(line: str) -> int:
    depth = 0
    count = 0
    for c in line:
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth <= 0:
            count += 1
    return count


def _body_range(code: list[str], start: int, indent: int) -> range:
    """Lines after ``start`` indented deeper than ``indent`` (blank lines included)."""
    j = start + 1
    end = j
    while j < len(code):
        if not code[j].strip():
            j += 1
            continue
        if _indent(code[j]) <= indent:
            break
        j += 1
        end = j
    return range(start + 1, end)


def module_names_for_path(path: str) -> set[str]:
    """Sibling folder names under the ``Content/Verse`` that contains ``path``.

    Each folder under Content/Verse is a Verse module, so a binding with the
    same name is ambiguous (Script error 3532). Returns an empty set when the
    path is not inside a Content/Verse tree or the folder cannot be listed.
    """
    import os

    if not path:
        return set()
    norm = path.replace("\\", "/")
    marker = "/Content/Verse/"
    idx = norm.find(marker)
    if idx < 0:
        return set()
    verse_root = norm[: idx + len(marker) - 1]
    try:
        return {
            name
            for name in os.listdir(verse_root)
            if os.path.isdir(os.path.join(verse_root, name)) and not name.startswith(".")
        }
    except OSError:
        return set()


class _Context:
    def __init__(self, source: str, module_names: set[str] | None = None) -> None:
        self.raw = source.splitlines()
        self.code = _strip_comments_and_strings(self.raw)
        self.module_names = set(module_names or ())
        self.funcs: list[_Func] = []
        self.sig_lines: set[int] = set()
        self.top_level_lines: set[int] = set()
        self.field_lines: list[int] = []
        self.usings: list[str] = []
        self._scan_structure()

    def _scan_structure(self) -> None:
        func_stack: list[int] = []
        class_stack: list[list[Any]] = []
        for i, line in enumerate(self.code):
            if not line.strip():
                continue
            m_using = _USING_RE.match(line)
            if m_using:
                self.usings.append(m_using.group(1))
                continue
            ind = _indent(line)
            while func_stack and ind <= func_stack[-1]:
                func_stack.pop()
            while class_stack and ind <= class_stack[-1][0]:
                class_stack.pop()
            in_func = bool(func_stack)
            if not in_func:
                self.top_level_lines.add(i)
            if class_stack and class_stack[-1][1] is None:
                class_stack[-1][1] = ind
            m_sig = _SIG_RE.match(line)
            if m_sig and m_sig.group(2) not in _KEYWORDS:
                effects = set(re.findall(r"<(\w+)>", m_sig.group(5))) & EFFECT_SPECIFIERS
                self.funcs.append(_Func(m_sig.group(2), i, ind, effects))
                self.sig_lines.add(i)
                func_stack.append(ind)
                continue
            if _CLASS_RE.search(line):
                class_stack.append([ind, None])
                continue
            if class_stack and not in_func and ind == class_stack[-1][1]:
                self.field_lines.append(i)

    def has_using(self, needle: str) -> bool:
        return any(needle in u for u in self.usings)

    def declared_as(self, name: str, type_name: str) -> bool:
        pat = re.compile(r"\b" + re.escape(name) + r"\s*:\s*" + re.escape(type_name) + r"\b")
        return any(pat.search(line) for line in self.code)


def _finding(line_idx: int, rule: str, severity: str, message: str, fix: str) -> dict:
    return {
        "line": line_idx + 1,
        "rule": rule,
        "severity": severity,
        "message": message,
        "fix": fix,
    }


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def _rule_no_rollback_in_failure_context(ctx: _Context) -> list[dict]:
    names = sorted({f.name for f in ctx.funcs if not f.effects})
    if not names:
        return []
    call_re = re.compile(r"(?<![\w.])(?:Self\.)?(" + "|".join(map(re.escape, names)) + r")\(")
    seen: set[tuple[int, str]] = set()
    out: list[dict] = []

    def report(line_idx: int, name: str) -> None:
        if (line_idx, name) in seen:
            return
        seen.add((line_idx, name))
        out.append(
            _finding(
                line_idx,
                "no_rollback_in_failure_context",
                "error",
                f"`{name}` has no effect specifier (default no_rollback) so it cannot be "
                "called in a failure context; declare it `<transacts>` or bind the result "
                "to a local before the `if`.",
                f"Add `<transacts>` after the parameter list of `{name}`, or "
                f"`Result := {name}(...)` on the line before the `if`/`[]`.",
            )
        )

    code = ctx.code
    for i, line in enumerate(code):
        if i in ctx.sig_lines:
            continue
        spans: list[tuple[int, int]] = []
        for m in re.finditer(r"\bif\s*\(", line):
            start = m.end() - 1
            spans.append((start, _match_close(line, start, "(", ")")))
        for m in re.finditer(r"\[", line):
            spans.append((m.start(), _match_close(line, m.start(), "[", "]")))
        for start, end in spans:
            for cm in call_re.finditer(line, start, end):
                report(i, cm.group(1))
        if _IF_HEAD_RE.search(line):
            ind = _indent(line)
            for j in _body_range(code, i, ind):
                if not code[j].strip():
                    continue
                if _THEN_ELSE_RE.match(code[j]):
                    break
                for cm in call_re.finditer(code[j]):
                    report(j, cm.group(1))
    return out


def _rule_reserved_underscore(ctx: _Context) -> list[dict]:
    out: list[dict] = []
    for i, line in enumerate(ctx.code):
        if any(r.search(line) for r in _UNDERSCORE_RES):
            out.append(
                _finding(
                    i,
                    "reserved_underscore_binding",
                    "error",
                    "`_` is reserved in Verse and cannot be used as a binding name.",
                    "Name the binding (e.g. `Unused`, `Index`) even if it is not read.",
                )
            )
    return out


def _rule_multiline_signature(ctx: _Context) -> list[dict]:
    out: list[dict] = []
    for i in sorted(ctx.top_level_lines):
        line = ctx.code[i]
        m = _SIG_START_RE.match(line)
        if not m or m.group(1) in _KEYWORDS:
            continue
        if line.count("(") > line.count(")"):
            out.append(
                _finding(
                    i,
                    "multiline_signature",
                    "error",
                    f"`{m.group(1)}(` opens a parameter list that is not closed on this "
                    "line; multi-line signatures leave a dangling `=` (Script error 3104).",
                    "Keep the whole signature — name, parameters, effects, `:type =` — on one line.",
                )
            )
    return out


def _rule_shadowed_builtin(ctx: _Context) -> list[dict]:
    out: list[dict] = []
    for i, line in enumerate(ctx.code):
        for m in _SHADOW_RE.finditer(line):
            name = m.group(1)
            out.append(
                _finding(
                    i,
                    "shadowed_builtin",
                    "warning",
                    f"`{name}` shadows a built-in function; later calls become ambiguous "
                    "(Script error 3588/3532).",
                    f"Rename the binding (e.g. `{name}Value`, `My{name}`).",
                )
            )
    return out


def _rule_field_initialiser_call(ctx: _Context) -> list[dict]:
    out: list[dict] = []
    for i in ctx.field_lines:
        m = _FIELD_CALL_RE.match(ctx.code[i])
        if not m:
            continue
        out.append(
            _finding(
                i,
                "field_initialiser_call",
                "error",
                f"Field initialiser calls `{m.group(1)}(`; field initialisers must be "
                "literals or archetypes (Script error 3582).",
                "Give the field a literal/archetype default and assign the computed value in OnBegin.",
            )
        )
    return out


def _rule_missing_using(ctx: _Context) -> list[dict]:
    out: list[dict] = []
    reported: set[str] = set()
    for pattern, needle, path in _USING_RULES:
        if ctx.has_using(needle):
            continue
        rx = re.compile(pattern)
        for i, line in enumerate(ctx.code):
            m = rx.search(line)
            if not m:
                continue
            if path in reported:
                break
            reported.add(path)
            token = m.group(0).rstrip("{( ").strip()
            out.append(
                _finding(
                    i,
                    "missing_using",
                    "error",
                    f"`{token}` is used but there is no `using {{ {path} }}`.",
                    f"Add `using {{ {path} }}` at the top of the file.",
                )
            )
            break
    return out


def _rule_array_block_tuples(ctx: _Context) -> list[dict]:
    out: list[dict] = []
    code = ctx.code
    for i, line in enumerate(code):
        if not _ARRAY_BLOCK_RE.search(line):
            continue
        ind = _indent(line)
        body = [j for j in _body_range(code, i, ind) if code[j].strip()]
        if not body:
            continue
        level = _indent(code[body[0]])
        for j in body:
            if _indent(code[j]) != level:
                continue
            if _ARCHETYPE_HEADER_RE.match(code[j]):
                continue
            if _top_level_commas(code[j]) > 0:
                out.append(
                    _finding(
                        i,
                        "array_block_commas",
                        "error",
                        "`array:` block lines contain top-level commas — each line becomes a "
                        "tuple, not an element.",
                        "Use `array{a, b, c}` (or one element per line without commas).",
                    )
                )
                break
    return out


def _rule_c_style_comment(ctx: _Context) -> list[dict]:
    out: list[dict] = []
    for i, line in enumerate(ctx.code):
        if _C_COMMENT_RE.search(line):
            out.append(
                _finding(
                    i,
                    "c_style_comment",
                    "error",
                    "C-style `//` or `/*` is not a Verse comment.",
                    "Use `#` for line comments and `<# ... #>` for block comments.",
                )
            )
    return out


def _rule_decides_set_without_transacts(ctx: _Context) -> list[dict]:
    out: list[dict] = []
    for f in ctx.funcs:
        if "decides" not in f.effects or "transacts" in f.effects:
            continue
        for j in _body_range(ctx.code, f.line, f.indent):
            if _SET_RE.search(ctx.code[j]):
                out.append(
                    _finding(
                        j,
                        "decides_set_without_transacts",
                        "error",
                        f"`{f.name}` is `<decides>` without `<transacts>` but its body uses `set`.",
                        f"Declare `{f.name}` as `<decides><transacts>`.",
                    )
                )
                break
    return out


def _rule_known_bad_api(ctx: _Context) -> list[dict]:
    out: list[dict] = []
    compiled = [(re.compile(p), msg, fix, sev) for p, msg, fix, sev in _BAD_API_RULES]
    for i, line in enumerate(ctx.code):
        for rx, msg, fix, sev in compiled:
            if rx.search(line):
                out.append(_finding(i, "unknown_api", sev, msg, fix))
        for m in _TOSTRING_RE.finditer(line):
            if ctx.declared_as(m.group(1), "logic"):
                out.append(
                    _finding(
                        i,
                        "unknown_api",
                        "error",
                        f"`ToString({m.group(1)})` — ToString has no `logic` overload.",
                        f'Use `if ({m.group(1)}?) then "true" else "false"`.',
                    )
                )
    return out


def _rule_decides_called_with_parens(ctx: _Context) -> list[dict]:
    names = sorted({f.name for f in ctx.funcs if "decides" in f.effects})
    if not names:
        return []
    call_re = re.compile(r"(?<![\w.])(?:Self\.)?(" + "|".join(map(re.escape, names)) + r")\(")
    out: list[dict] = []
    for i, line in enumerate(ctx.code):
        if i in ctx.sig_lines:
            continue
        for m in call_re.finditer(line):
            out.append(
                _finding(
                    i,
                    "decides_called_with_parens",
                    "error",
                    f"`{m.group(1)}` is `<decides>` and must be called with `[]`, not `()`.",
                    f"Write `{m.group(1)}[...]` inside a failure context (`if`, `for`, `[]`).",
                )
            )
    return out


def _rule_int_division(ctx: _Context) -> list[dict]:
    out: list[dict] = []
    for i, line in enumerate(ctx.code):
        if "Floor[" in line or "Int[" in line:
            continue
        m = _INT_DIV_RE.search(line)
        if not m:
            continue
        operands = (m.group(1), m.group(2))
        if all(op.isdigit() or ctx.declared_as(op, "int") for op in operands):
            out.append(
                _finding(
                    i,
                    "int_division",
                    "warning",
                    "`/` on two ints yields `rational` and needs a failure context "
                    "(Script error 3512/3509).",
                    "Use `Floor[A / B]` inside an `if`, or convert to float first (`A * 1.0 / B`).",
                )
            )
    return out


def _rule_lone_braces_after_if(ctx: _Context) -> list[dict]:
    out: list[dict] = []
    code = ctx.code
    for i, line in enumerate(code):
        if not _IF_ELSE_HEAD_RE.match(line):
            continue
        j = i + 1
        while j < len(code) and not code[j].strip():
            j += 1
        if j >= len(code):
            continue
        if _indent(code[j]) > _indent(line) and _LONE_BRACES_RE.match(code[j]):
            out.append(
                _finding(
                    j,
                    "lone_braces_block",
                    "error",
                    "A line that is only `{}` under `if (...):`/`else:` is a parse error "
                    "(Script error 3100: Expected expression, got { in indented block).",
                    "Put `{}` on the same line as the head: `if (set M[K] = V) {}`.",
                )
            )
    return out


def _rule_module_name_shadow(ctx: _Context) -> list[dict]:
    names = sorted(n for n in ctx.module_names if re.fullmatch(r"[A-Za-z_]\w*", n))
    if not names:
        return []
    rx = re.compile(r"(?<![\w.@?])(?:var\s+)?(" + "|".join(map(re.escape, names)) + r")\s*:")
    out: list[dict] = []
    for i, line in enumerate(ctx.code):
        if _MODULE_DECL_RE.search(line):
            continue
        for m in rx.finditer(line):
            out.append(
                _finding(
                    i,
                    "module_name_shadow",
                    "warning",
                    f"`{m.group(1)}` is also a Verse module folder under Content/Verse; "
                    "the binding is ambiguous with the module (Script error 3532).",
                    f"Rename the binding (e.g. `{m.group(1)}Ref`, `My{m.group(1)}`).",
                )
            )
    return out


def _rule_missing_fortnite_ui_using(ctx: _Context) -> list[dict]:
    if ctx.has_using(_FORTNITE_UI_USING):
        return []
    for i, line in enumerate(ctx.code):
        m = _UI_TOKEN_RE.search(line)
        if m:
            return [
                _finding(
                    i,
                    "missing_fortnite_ui_using",
                    "error",
                    f"`{m.group(1)}` needs `using {{ {_FORTNITE_UI_USING} }}` "
                    "(`/UnrealEngine.com/Temporary/UI` alone is not enough).",
                    f"Add `using {{ {_FORTNITE_UI_USING} }}` at the top of the file.",
                )
            ]
    return []


_CONCRETE_SUBTYPE_RE = re.compile(r"concrete_subtype\s*\(\s*(\w+)\s*\)")


def _rule_concrete_subtype_editable(ctx: _Context) -> list[dict]:
    """L16 — v42.10 validator: every class placed in a concrete_subtype field must be <concrete>."""
    out: list[dict] = []
    for i, line in enumerate(ctx.code):
        m = _CONCRETE_SUBTYPE_RE.search(line)
        if not m:
            continue
        prev = ctx.code[i - 1] if i > 0 else ""
        if "@editable" not in line and "@editable" not in prev:
            continue
        out.append(
            _finding(
                i,
                "concrete_subtype_editable",
                "warning",
                f"`concrete_subtype({m.group(1)})` @editable: from v42.10 the editor validates that "
                "every class assigned to this field is itself `<concrete>` (all fields defaulted); "
                "a non-concrete class fails validation on republish.",
                "Declare your own candidate classes `class<concrete>` (stock /Fortnite.com/Weapons "
                "and /Fortnite.com/Items classes already are), or drop the field.",
            )
        )
    return out


_RULES = (
    _rule_no_rollback_in_failure_context,
    _rule_reserved_underscore,
    _rule_multiline_signature,
    _rule_shadowed_builtin,
    _rule_field_initialiser_call,
    _rule_missing_using,
    _rule_array_block_tuples,
    _rule_c_style_comment,
    _rule_decides_set_without_transacts,
    _rule_known_bad_api,
    _rule_decides_called_with_parens,
    _rule_int_division,
    _rule_lone_braces_after_if,
    _rule_module_name_shadow,
    _rule_missing_fortnite_ui_using,
    _rule_concrete_subtype_editable,
)


def lint_verse(
    source: str,
    relative_path: str = "",
    module_names: set[str] | None = None,
) -> list[dict]:
    """Return heuristic findings for one Verse source (never raises).

    ``module_names`` — folder names under Content/Verse (each is a module);
    when omitted and ``relative_path`` is an absolute path inside a
    Content/Verse tree, they are derived from disk.
    """
    if relative_path and relative_path.lower().endswith(".digest.verse"):
        return []
    try:
        if module_names is None and relative_path:
            module_names = module_names_for_path(relative_path)
        ctx = _Context(source or "", module_names)
        findings: list[dict] = []
        for rule in _RULES:
            findings.extend(rule(ctx))
    except Exception:  # noqa: BLE001 — a linter bug must never break a write
        return []
    findings.sort(key=lambda f: (f["line"], f["rule"]))
    return findings


def summarize_findings(findings: list[dict]) -> dict:
    """``{"errors": n, "warnings": n, "findings": [...]}`` for tool payloads."""
    return {
        "errors": sum(1 for f in findings if f.get("severity") == "error"),
        "warnings": sum(1 for f in findings if f.get("severity") != "error"),
        "findings": findings,
    }
