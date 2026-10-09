"""Rules a plugin draft must pass before it installs (``ducky_plugin_validate``),
each problem with a message that says how to fix it.

- UI files (HTML/CSS/JS outside ``backend/`` and ``skills/``): no color literals
  (hex, ``rgb()``/``hsl()``/…, named colors) and no ``var(--x)`` that isn't an
  Appearance variable (:mod:`appearance_vars`) or a custom property the plugin
  defines itself. Theme files the plugin contributes (``appearance.*``) and
  vendored libraries (a ``vendor/`` folder, ``*.min.js`` / ``*.min.css``) are
  exempt. ``transparent`` and ``currentColor`` are not colors of their own.
- Every panel RPC action has an MCP tool, at least one workflow node (handled in
  the backend) and a template that uses it, a bundled ``skills/<id>/SKILL.md``,
  and ``:focus-visible`` styles when there is a panel.
- The backend never reads its own ``.py`` source or loads ``.py`` files by path:
  installed plugins can be compiled, and those files are gone then.
"""

from __future__ import annotations

import ast
import difflib
import re
from pathlib import Path
from typing import Any, Iterable

UI_SUFFIXES = frozenset({".html", ".htm", ".css", ".js", ".mjs"})
_SKIP_PARTS = frozenset({"backend", "skills", "scripts", "deploy", ".git", "__pycache__", ".venv", "node_modules"})
MAX_UI_ISSUES = 30
KIT_HINT = 'link the UI kit (<script src="../../_kit/ducky.js"></script>, see the ai_plugins reference)'

# CSS named colors (CSS Color 4), minus transparent / currentcolor which hold no color.
NAMED_COLORS = frozenset("""
aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet brown
burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan
darkgoldenrod darkgray darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid
darkred darksalmon darkseagreen darkslateblue darkslategray darkslategrey darkturquoise darkviolet
deeppink deepskyblue dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro
ghostwhite gold goldenrod gray green greenyellow grey honeydew hotpink indianred indigo ivory khaki
lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan lightgoldenrodyellow
lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen lightskyblue lightslategray
lightslategrey lightsteelblue lightyellow lime limegreen linen magenta maroon mediumaquamarine
mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen mediumturquoise
mediumvioletred midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive olivedrab
orange orangered orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff peru
pink plum powderblue purple rebeccapurple red rosybrown royalblue saddlebrown salmon sandybrown
seagreen seashell sienna silver skyblue slateblue slategray slategrey snow springgreen steelblue tan
teal thistle tomato turquoise violet wheat white whitesmoke yellow yellowgreen
""".split())

# Properties whose values are colors (named colors are only looked for in these).
_COLOR_PROPS = re.compile(
    r"^(?:--.*|color|background(?:-color)?|border(?:-(?:top|right|bottom|left|block|inline)(?:-start|-end)?)?"
    r"(?:-color)?|outline(?:-color)?|box-shadow|text-shadow|caret-color|accent-color|fill|stroke|"
    r"column-rule(?:-color)?|text-decoration(?:-color)?|text-emphasis(?:-color)?|scrollbar-color|"
    r"stop-color|flood-color|lighting-color)$",
    re.IGNORECASE,
)
_HEX = re.compile(r"(?<![\w&])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![\w-])")
_COLOR_FN = re.compile(r"\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(\s*(?!var\()[-+.\d]", re.IGNORECASE)
_WORD = re.compile(r"[a-zA-Z]+")
_VAR_USE = re.compile(r"var\(\s*--([\w-]+)")
_VAR_DEF = re.compile(r"(?:^|[;{\s\"'`])--([\w-]+)\s*:")
_JS_VAR_DEF = re.compile(r"setProperty\(\s*[\"'`]--([\w-]+)")
_STRIP_VALUE = re.compile(r"url\([^)]*\)|\"[^\"]*\"|'[^']*'")
_COMMENT_CSS = re.compile(r"/\*.*?\*/", re.DOTALL)
_BLOCK = re.compile(r"\{([^{}]*)\}")
_DECL = re.compile(r"(--[\w-]+|[a-zA-Z-]+)\s*:\s*([^;]+)")
_STYLE_TAG = re.compile(r"<style\b[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)
_SCRIPT_TAG = re.compile(r"<script\b(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)
_STYLE_ATTR = re.compile(r"\sstyle\s*=\s*(\"[^\"]*\"|'[^']*')", re.IGNORECASE)
_SVG_ATTR = re.compile(r"\s(fill|stroke|stop-color|flood-color|color|bgcolor)\s*=\s*(\"[^\"]*\"|'[^']*')", re.IGNORECASE)
# JS: a color in a string handed to a style or canvas property, or a CSS declaration in a string.
_JS_PROP = re.compile(
    r"\b(color|background|backgroundColor|borderColor|border|outline|outlineColor|fill|stroke|fillStyle|"
    r"strokeStyle|shadowColor|boxShadow|textShadow|caretColor|accentColor)\s*[:=]\s*([\"'`])([^\"'`]*)\2"
)
_JS_SET_PROPERTY = re.compile(r"setProperty\(\s*[\"'`][^\"'`]+[\"'`]\s*,\s*([\"'`])([^\"'`]*)\1")
_JS_DECL = re.compile(
    r"\b(color|background(?:-color)?|border(?:-(?:top|right|bottom|left))?(?:-color)?|outline(?:-color)?|fill|"
    r"stroke|box-shadow|text-shadow|caret-color|accent-color)\s*:\s*([^;\"'`}\n]+)",
    re.IGNORECASE,
)
_FOCUS_VISIBLE = re.compile(r":focus-visible\b")
_KIT_LINK = re.compile(r"_kit/ducky\.(?:css|js)")


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _literals(value: str, *, named: bool) -> list[str]:
    """Color literals in one CSS value (``url()`` and quoted text skipped)."""
    clean = _STRIP_VALUE.sub(" ", value)
    found = [m.group(0) for m in _HEX.finditer(clean)]
    found += [m.group(0).rstrip("(-+.0123456789 ") + "()" for m in _COLOR_FN.finditer(clean)]
    if named:
        found += [w for w in _WORD.findall(_VAR_USE.sub(" ", clean)) if w.lower() in NAMED_COLORS]
    return found


_EXTERNAL_TAG = re.compile(r"<(script|link)\b([^>]*)>", re.IGNORECASE)
_EXTERNAL_URL = re.compile(r"""\b(?:src|href)\s*=\s*["'](https?://([^"'/?#]+)[^"']*)""", re.IGNORECASE)
_REMOTE_IMPORT = re.compile(
    r"""(?:\bimport\s*\(\s*|\bimport\b[^;"'`]*?\bfrom\s*|\bimport\s+)["'`](https?://[^"'`\s]+)""", re.IGNORECASE
)
_VENDOR_HINT = (
    "Plugins never load code from the internet: download the package (its built .js/.css "
    "from npm or the project's release) into the plugin's ui/vendor/ folder and load it from there "
    "(<script src=\"vendor/lib.min.js\">), so the panel works offline and can't change after publishing."
)


def _allowed_script_hosts() -> set[str]:
    """Hosts the plugin panel's Content-Security-Policy lets scripts load from."""
    from backend.uefn_plugins.webview import PLUGIN_UI_HTML_CSP

    for part in PLUGIN_UI_HTML_CSP.split(";"):
        words = part.split()
        if words and words[0] == "script-src":
            return {w.split("://", 1)[1].lower() for w in words[1:] if "://" in w}
    return set()


class _Scan:
    """Issues and custom properties gathered across a plugin's UI files."""

    def __init__(self) -> None:
        self.blocked: list[str] = []
        self.literals: list[str] = []
        self.uses: list[tuple[str, str, int]] = []  # (var name, file, line)
        self.defined: set[str] = set()
        self.focus = False
        self.kit = False
        self._seen: set[tuple[str, int, str]] = set()

    def literal(self, rel: str, line: int, found: Iterable[str], where: str) -> None:
        for value in dict.fromkeys(found):
            if (rel, line, value) in self._seen:
                continue
            self._seen.add((rel, line, value))
            self.literals.append(
                f"{rel}:{line}: color literal {value!r} in {where}. Use an Appearance variable instead "
                f"(var(--fg), var(--fg-dim), var(--muted), var(--bg), var(--card), var(--border), var(--accent), "
                f"var(--red), var(--green), …) or {KIT_HINT}."
            )

    def vars_in(self, rel: str, text: str, base_line: int = 1) -> None:
        for m in _VAR_USE.finditer(text):
            self.uses.append((m.group(1), rel, base_line + _line(text, m.start()) - 1))

    def css(self, rel: str, text: str, base_line: int = 1) -> None:
        text = _COMMENT_CSS.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
        self.focus = self.focus or bool(_FOCUS_VISIBLE.search(text))
        self.vars_in(rel, text, base_line)
        self.defined.update(m.group(1) for m in _VAR_DEF.finditer(text))
        for block in _BLOCK.finditer(text):
            self.declarations(rel, block.group(1), base_line + _line(text, block.start(1)) - 1)

    def declarations(self, rel: str, body: str, base_line: int) -> None:
        for decl in _DECL.finditer(body):
            prop, value = decl.group(1), decl.group(2)
            found = _literals(value, named=bool(_COLOR_PROPS.match(prop)))
            if found:
                self.literal(rel, base_line + _line(body, decl.start()) - 1, found, prop)

    def js(self, rel: str, text: str, base_line: int = 1) -> None:
        self.vars_in(rel, text, base_line)
        self.defined.update(m.group(1) for m in _JS_VAR_DEF.finditer(text))
        self.focus = self.focus or bool(_FOCUS_VISIBLE.search(text))
        for m in _JS_PROP.finditer(text):
            found = _literals(m.group(3), named=True)
            if found:
                self.literal(rel, base_line + _line(text, m.start()) - 1, found, m.group(1))
        for m in _JS_SET_PROPERTY.finditer(text):
            found = _literals(m.group(2), named=True)
            if found:
                self.literal(rel, base_line + _line(text, m.start()) - 1, found, "setProperty")
        for m in _JS_DECL.finditer(text):
            found = _literals(m.group(2), named=True)
            if found:
                self.literal(rel, base_line + _line(text, m.start()) - 1, found, m.group(1))
        for m in _COLOR_FN.finditer(text):
            self.literal(rel, base_line + _line(text, m.start()) - 1, [m.group(0).split("(")[0] + "()"], "script")

    def external(self, rel: str, text: str) -> None:
        """Code or styles loaded from the internet: plugin panels block all of it."""
        allowed = _allowed_script_hosts()  # empty today: no outside code at all
        for tag in _EXTERNAL_TAG.finditer(text):
            kind, attrs = tag.group(1).lower(), tag.group(2)
            url = _EXTERNAL_URL.search(attrs)
            if not url:
                continue
            host = url.group(2).lower()
            line = _line(text, tag.start())
            if kind == "script" and host not in allowed:
                self.blocked.append(f"{rel}:{line}: <script src> from {host}. {_VENDOR_HINT}")
            elif kind == "link" and "stylesheet" in attrs.lower():
                self.blocked.append(f"{rel}:{line}: stylesheet from {host}. {_VENDOR_HINT}")
        self.remote_imports(rel, text)

    def remote_imports(self, rel: str, text: str, base_line: int = 1) -> None:
        """``import … from "https://…"`` / ``import("https://…")`` in scripts."""
        for m in _REMOTE_IMPORT.finditer(text):
            line = base_line + _line(text, m.start()) - 1
            self.blocked.append(f"{rel}:{line}: imports code from {m.group(1)}. {_VENDOR_HINT}")

    def html(self, rel: str, text: str) -> None:
        self.external(rel, text)
        self.kit = self.kit or bool(_KIT_LINK.search(text))
        for m in _STYLE_TAG.finditer(text):
            self.css(rel, m.group(1), _line(text, m.start(1)))
        for m in _SCRIPT_TAG.finditer(text):
            self.js(rel, m.group(1), _line(text, m.start(1)))
        for m in _STYLE_ATTR.finditer(text):
            body = m.group(1)[1:-1]
            self.vars_in(rel, body, _line(text, m.start()))
            self.defined.update(d.group(1) for d in _VAR_DEF.finditer(";" + body))
            self.declarations(rel, body, _line(text, m.start()))
        for m in _SVG_ATTR.finditer(text):
            found = _literals(m.group(2)[1:-1], named=True)
            if found:
                self.literal(rel, _line(text, m.start()), found, f"{m.group(1)}= attribute")


def _theme_entries(manifest: dict[str, Any]) -> set[str]:
    """Files the plugin contributes as app themes (``appearance.*``): they define colors."""
    out: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str) and Path(node).suffix.lower() in UI_SUFFIXES:
            out.add(node.replace("\\", "/").lstrip("./"))

    contributes = manifest.get("contributes") if isinstance(manifest.get("contributes"), dict) else {}
    for key, value in contributes.items():
        if str(key).startswith("appearance."):
            walk(value)
    return out


def ui_files(root: Path, manifest: dict[str, Any]) -> list[tuple[str, Path]]:
    """``(relative path, file)`` of the plugin's own UI files, the ones the rules check."""
    themes = _theme_entries(manifest)
    out: list[tuple[str, Path]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in UI_SUFFIXES:
            continue
        rel_parts = path.relative_to(root).parts
        rel = "/".join(rel_parts)
        name = path.name.lower()
        if (rel_parts[0] in _SKIP_PARTS or "vendor" in rel_parts or name.endswith((".min.js", ".min.css"))
                or rel in themes):
            continue
        out.append((rel, path))
    return out


def has_panels(manifest: dict[str, Any]) -> bool:
    contributes = manifest.get("contributes") if isinstance(manifest.get("contributes"), dict) else {}
    return bool(contributes.get("ui.panels") or contributes.get("ui_panels"))


def check_ui(root: Path, manifest: dict[str, Any]) -> list[str]:
    """Color literals, unknown ``var(--x)``, and a focus style when there are panels."""
    from backend.uefn_plugins.appearance_vars import NAMES

    scan = _Scan()
    for rel, path in ui_files(root, manifest):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        suffix = path.suffix.lower()
        if suffix in (".html", ".htm"):
            scan.html(rel, text)
        elif suffix == ".css":
            scan.css(rel, text)
        else:
            scan.js(rel, text)
            scan.remote_imports(rel, text)
    issues = list(scan.blocked) + list(scan.literals)
    for name, rel, line in scan.uses:
        if name in NAMES or name in scan.defined:
            continue
        near = difflib.get_close_matches(name, NAMES, n=1)
        hint = f" Did you mean var(--{near[0]})?" if near else ""
        issues.append(
            f"{rel}:{line}: var(--{name}) isn't an Appearance variable the app sets.{hint} "
            f"Use one of the app's (var(--fg), var(--bg), var(--card), var(--accent), var(--font-ui), …) "
            f"or define --{name} in your own CSS."
        )
    issues = list(dict.fromkeys(issues))
    if len(issues) > MAX_UI_ISSUES:
        issues = issues[:MAX_UI_ISSUES] + [f"…and {len(issues) - MAX_UI_ISSUES} more like these."]
    if has_panels(manifest) and not (scan.focus or scan.kit):
        issues.append(
            "Panels need a visible keyboard focus: add "
            "button:focus-visible { outline: 2px solid var(--border-focus); outline-offset: 2px; } "
            f"(and the same for inputs, links, tabs), or {KIT_HINT}."
        )
    return issues


# --------------------------------------------------------------------------- backend


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _first_str(call: ast.Call) -> str:
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    return ""


def _is_own_file(expr: ast.AST) -> bool:
    """``__file__``, ``Path(__file__)`` or that ``.resolve()``d: the module's own source."""
    while (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute)
           and expr.func.attr in ("resolve", "absolute")):
        expr = expr.func.value
    if isinstance(expr, ast.Call) and _call_name(expr.func) in ("Path", "PurePath") and expr.args:
        expr = expr.args[0]
    return isinstance(expr, ast.Name) and expr.id == "__file__"


def _kw_str(call: ast.Call, key: str) -> str:
    for kw in call.keywords:
        if kw.arg == key and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return ""


class _Backend:
    """What a plugin's backend registers, read from its source (never imported)."""

    def __init__(self) -> None:
        self.tools: set[str] = set()
        self.rpcs: set[str] = set()
        self.nodes: set[str] = set()
        self.source_reads: list[str] = []

    def scan(self, rel: str, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for deco in node.decorator_list:
                    target = deco.func if isinstance(deco, ast.Call) else deco
                    if _call_name(target) == "tool":
                        named = _kw_str(deco, "name") if isinstance(deco, ast.Call) else ""
                        self.tools.add(named or node.name)
                    elif _call_name(target) in ("register_pipeline_node", "register_automation_node"):
                        if isinstance(deco, ast.Call) and _first_str(deco):
                            self.nodes.add(_first_str(deco))
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node.func)
            if name == "register_panel_rpc" and _first_str(node):
                self.rpcs.add(_first_str(node))
            elif name in ("register_pipeline_node", "register_automation_node") and _first_str(node):
                self.nodes.add(_first_str(node))
            elif name == "tool" and node.args and isinstance(node.args[0], ast.Name):
                self.tools.add(_kw_str(node, "name") or node.args[0].id)
            self._source_read(rel, node, name)

    def _source_read(self, rel: str, call: ast.Call, name: str) -> None:
        line = getattr(call, "lineno", 0)
        if name == "reload":
            self.source_reads.append(
                f"{rel}:{line}: importlib.reload() fails in the compiled build the Store ships (SystemError), "
                "and the call that does it breaks with it. Reload only when running from source "
                "(skip it when '__compiled__' in globals()), or don't reload at all."
            )
            return
        if name in ("spec_from_file_location", "run_path", "SourceFileLoader", "load_source"):
            self.source_reads.append(
                f"{rel}:{line}: loads a .py file by path ({name}). Installed plugins can be compiled and "
                "those files are gone then: import the module instead (from . import helpers)."
            )
            return
        if name in ("getsource", "getsourcelines", "getsourcefile", "findsource"):
            self.source_reads.append(
                f"{rel}:{line}: reads Python source ({name}). It isn't there once the plugin is compiled: "
                "keep what you need as data instead."
            )
            return
        if name not in ("open", "read_text", "read_bytes"):
            return
        if isinstance(call.func, ast.Name):
            target = call.args[0] if call.args else None  # open(path)
        else:
            target = call.func.value if isinstance(call.func, ast.Attribute) else None  # Path(...).read_text()
        strings = [n.value for n in ast.walk(call) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        reads_py = any(s.lower().endswith(".py") for s in strings)
        own_file = target is not None and _is_own_file(target)
        if reads_py or own_file:
            self.source_reads.append(
                f"{rel}:{line}: reads a .py file{' (its own source)' if own_file else ''}. Installed plugins "
                "can be compiled and the .py files are gone then: import the module, or keep the data in a "
                ".json file next to it."
            )


def scan_backend(root: Path) -> _Backend:
    found = _Backend()
    backend = root / "backend"
    if not backend.is_dir():
        return found
    for py in sorted(backend.rglob("*.py")):
        if any(part in _SKIP_PARTS - {"backend"} for part in py.relative_to(root).parts):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
            continue  # py_compile reports it
        found.scan(py.relative_to(root).as_posix(), tree)
    return found


def _types_in(node: Any) -> set[str]:
    """Every node ``type`` in a template (its graph, or a bundle's workflows)."""
    out: set[str] = set()
    if isinstance(node, dict):
        if isinstance(node.get("type"), str):
            out.add(node["type"])
        for value in node.values():
            out |= _types_in(value)
    elif isinstance(node, list):
        for value in node:
            out |= _types_in(value)
    return out


def check_backend(root: Path, manifest: dict[str, Any], plugin_id: str) -> list[str]:
    """Tools for panel RPCs, a handled workflow node and its template, no source reads."""
    found = scan_backend(root)
    fn = plugin_id.replace("-", "_")
    issues: list[str] = []
    for rpc in sorted(found.rpcs):
        want = re.sub(r"[^a-z0-9]+", "_", rpc.lower()).strip("_")
        if not any(tool == want or tool.endswith(f"_{want}") for tool in found.tools):
            issues.append(
                f"Panel RPC {rpc!r} has no MCP tool. Add @api.tool() def {fn}_{want}(...) that calls the same "
                "function as the panel, so a chat can do everything the panel can."
            )
    contributes = manifest.get("contributes") if isinstance(manifest.get("contributes"), dict) else {}
    automations = contributes.get("automations") if isinstance(contributes.get("automations"), dict) else {}
    declared = {
        str(n.get("id") or "").strip()
        for n in automations.get("nodes") or []
        if isinstance(n, dict) and str(n.get("id") or "").strip()
    }
    if not declared:
        issues.append(
            f"Add a workflow node: contributes.automations.nodes [{{\"id\": \"{plugin_id}.run\", ...}}] and "
            f"@api.register_pipeline_node(\"{plugin_id}.run\") calling the same functions as your tools."
        )
    for node_id in sorted(declared - found.nodes):
        issues.append(
            f"Workflow node {node_id!r} is declared but nothing handles it: add "
            f"@api.register_pipeline_node({node_id!r}) in the backend."
        )
    templates = [t for t in automations.get("templates") or [] if isinstance(t, dict)]
    if declared and not any(_types_in(t) & declared for t in templates):
        node_id = sorted(declared)[0]
        issues.append(
            f"Add a workflow template that uses your node: contributes.automations.templates with a graph "
            f"start.chat → pipeline.agent → {node_id} → pipeline.finish."
        )
    issues.extend(found.source_reads)
    return issues


def check_skill(root: Path, plugin_id: str) -> list[str]:
    if (root / "skills" / plugin_id / "SKILL.md").is_file():
        return []
    return [
        f"Add skills/{plugin_id}/SKILL.md (front matter name: {plugin_id}, then what each tool does and "
        "when to use it) so later chats know the plugin's tools."
    ]


def lint_draft(root: Path, manifest: dict[str, Any], plugin_id: str) -> list[str]:
    """Every rule above, in the order a fix is easiest to follow."""
    return check_skill(root, plugin_id) + check_backend(root, manifest, plugin_id) + check_ui(root, manifest)
