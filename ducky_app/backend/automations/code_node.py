"""Custom code nodes (``code.js``): the step the runner runs, and the ``ducky`` calls
its JavaScript makes (MCP tools, built-in nodes, expressions, templates, its log)."""

from __future__ import annotations

import hashlib
import itertools
import threading
from typing import Any

from backend.automations import catalog, jsrt, plugin
from backend.automations import runner as _runner
from backend.automations.code_api import BLANK_CODE, FLOW_TYPES, NOT_BUILTINS
from backend.automations.expr import ExprError, evaluate
from backend.automations.pins import clean_pins
from backend.automations.store import LOCAL, get_workflow

CODE_TYPE = "code.js"
_SPEND_KEYS = ("spend", "confirm_spend")
_HostError = jsrt.HostError


def code_sha(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _is_local(wf: dict[str, Any]) -> bool:
    return str((wf.get("owner") or {}).get("kind") or LOCAL) == LOCAL


def declared(cfg: dict[str, Any], code: str) -> dict[str, Any]:
    """What the code declares (pins, settings, the tools and nodes it may use) and its
    problems: what the save wrote into config when it is this code, else checked now."""
    sha = code_sha(code)
    last_good = {
        "pins": cfg.get("pins") if isinstance(cfg.get("pins"), dict) else {},
        "settings_spec": cfg.get("settings_spec") if isinstance(cfg.get("settings_spec"), list) else [],
        "uses": cfg.get("uses") if isinstance(cfg.get("uses"), dict) else {},
    }
    if cfg.get("code_sha") == sha and isinstance(cfg.get("pins"), dict):
        return {"code_sha": sha, "problems": list(cfg.get("problems") or []), **last_good}
    try:
        from backend.automations import code_check
    except ImportError:
        return {"code_sha": sha, "problems": jsrt.check_syntax(code), **last_good}
    checked = code_check.check(code)
    out = {"code_sha": sha, "problems": list(checked.get("problems") or []), **last_good}
    if checked.get("ok"):
        out.update(pins=checked.get("pins") or {}, settings_spec=checked.get("settings_spec") or [], uses=checked.get("uses") or {})
    return out


def _first_error(problems: list[Any]) -> dict[str, Any] | None:
    for problem in problems:
        if isinstance(problem, dict) and str(problem.get("severity") or "error") == "error":
            return problem
    return None


def _settings(spec: list[Any], values: Any) -> dict[str, Any]:
    out = {str(field["id"]): field["default"] for field in spec if isinstance(field, dict) and field.get("id") and "default" in field}
    out.update(values if isinstance(values, dict) else {})
    return out


def literal_strings(code: str) -> set[str]:
    """Every plain string literal in the code: "…", '…', and `…` without ${…}. Comments are skipped."""
    out: set[str] = set()
    i, n = 0, len(code)
    escapes = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
    while i < n:
        ch = code[i]
        if code.startswith("//", i):
            end = code.find("\n", i)
            i = n if end < 0 else end
        elif code.startswith("/*", i):
            end = code.find("*/", i + 2)
            i = n if end < 0 else end + 2
        elif ch in "\"'`":
            i += 1
            chars: list[str] = []
            plain = True
            while i < n and code[i] != ch:
                c = code[i]
                if c == "\\" and i + 1 < n:
                    nxt = code[i + 1]
                    if nxt == "u" and code[i + 2:i + 6].isalnum() and len(code[i + 2:i + 6]) == 4:
                        try:
                            chars.append(chr(int(code[i + 2:i + 6], 16)))
                            i += 6
                            continue
                        except ValueError:
                            pass
                    if nxt == "\n":
                        i += 2
                        continue
                    chars.append(escapes.get(nxt, nxt))
                    i += 2
                    continue
                if ch == "`" and code.startswith("${", i):
                    plain = False
                if ch != "`" and c == "\n":
                    plain = False  # not a valid string: stop at the line end
                    break
                chars.append(c)
                i += 1
            i += 1
            if plain:
                out.add("".join(chars))
        else:
            i += 1
    return out


def _gate(wf: dict[str, Any], node: dict[str, Any], ctx: dict[str, Any]) -> str | None:
    try:
        from backend.automations import code_approval
    except ImportError:
        return "Custom code can't run: its approvals are missing on this PC."
    return code_approval.gate(wf, node, ctx)


def _review_text() -> str:
    try:
        from backend.automations.code_approval import REVIEW
    except ImportError:
        return ""
    return REVIEW


def _approved(workflow_id: str, node_id: str, sha: str) -> bool:
    try:
        from backend.automations import code_approval
    except ImportError:
        return False
    return bool(code_approval.is_approved(workflow_id, node_id, sha))


def _outputs_of(step: dict[str, Any], spec: dict[str, Any] | None) -> dict[str, Any]:
    """What a built-in node made, like its card shows: its outputs, else its result."""
    if isinstance(step.get("outputs"), dict):
        return dict(step["outputs"])
    result = step.get("result") if isinstance(step.get("result"), dict) else {}
    pins = clean_pins((spec or {}).get("outputs"))
    return {pin["id"]: result.get(pin["id"]) for pin in pins} if pins else dict(result)


class _Host:
    """The Python side of one code step's ``ducky`` calls."""

    def __init__(self, node: dict[str, Any], ctx: dict[str, Any], wf: dict[str, Any], code: str, decl: dict[str, Any],
                 *, dry_run: bool, calls: list[dict[str, Any]]):
        self.node = node
        self.nid = str(node.get("id") or "")
        self.cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
        self.ctx = ctx
        self.wf = wf
        self.code = code
        self.sha = str(decl["code_sha"])
        uses = decl.get("uses") or {}
        self.tools = {str(name) for name in uses.get("tools") or []}
        self.builtins = {str(name) for name in uses.get("builtins") or []}
        self.dry_run = dry_run
        self.calls = calls
        self.counter = itertools.count(1)
        self.lock = threading.Lock()

    def functions(self) -> dict[str, Any]:
        return {"tool": self.tool, "builtin": self.builtin, "expr": self.expr, "template": self.template, "log": self.log}

    def _record(self, entry: dict[str, Any]) -> None:
        with self.lock:
            if len(self.calls) < 200:
                self.calls.append(entry)

    def tool(self, name: Any, args: Any = None) -> Any:
        name = str(name or "").strip()
        if name not in self.tools:
            raise _HostError(f"{name or 'That tool'} isn't in node.tools: add it there to call it.")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise _HostError(f"{name}: arguments must be an object.")
        args = {key: value for key, value in args.items() if key not in _SPEND_KEYS}
        self._record({"name": name, "args": args, "dry_run": self.dry_run})
        if self.dry_run:
            return {"ok": True, "dry_run": True}
        called = _runner._invoke_tool(name, args, self.nid, self._typed(name, args))
        if not called.get("ok"):
            raise _HostError(str(called.get("error") or f"{name} failed"))
        out = called.get("result") or {}
        return out.get("data") if "data" in out else out.get("output")

    def _typed(self, name: str, args: dict[str, Any]) -> str:
        """ducky_terminal_run skips the Allow/Deny pop-up only for a command written into
        approved code as a plain string, in a Local workflow."""
        command = args.get("command")
        if name != "ducky_terminal_run" or not isinstance(command, str) or not command.strip():
            return ""
        if not _is_local(self.wf) or command not in literal_strings(self.code):
            return ""
        return command if _approved(str(self.wf.get("id") or ""), self.nid, self.sha) else ""

    def builtin(self, ntype: Any, values: Any = None, config: Any = None) -> dict[str, Any]:
        ntype = str(ntype or "").strip()
        if ntype in FLOW_TYPES:
            raise _HostError(f"{ntype} steers the run, so code can't run it.")
        if ntype in NOT_BUILTINS:
            raise _HostError(NOT_BUILTINS[ntype])
        if ntype not in self.builtins:
            raise _HostError(f"{ntype or 'That node'} isn't in node.builtins: add it there to run it.")
        values = {} if values is None else values
        config = {} if config is None else config
        if not isinstance(values, dict) or not isinstance(config, dict):
            raise _HostError(f"{ntype}: input and config must be objects.")
        config = {key: value for key, value in config.items() if key not in _SPEND_KEYS}
        if self.cfg.get("spend") is True:
            config["spend"] = True  # this node's own Spend switch
        self._record({"name": ntype, "args": {"input": values, "config": config}, "dry_run": self.dry_run, "builtin": True})
        if self.dry_run:
            return {"ok": True, "dry_run": True}
        spec = catalog.node_specs().get(ntype)
        if spec is None and plugin.get_handler(ntype) is None:
            raise _HostError(f"There is no {ntype} node on this PC.")
        synthetic = {"id": f"{self.nid}-{next(self.counter)}", "type": ntype, "label": str((spec or {}).get("label") or ntype), "config": config}
        token = _runner._FROM_CODE.set(True)
        try:
            step = _runner._exec_node(synthetic, self.ctx, dict(values))
        finally:
            _runner._FROM_CODE.reset(token)
        if not step.get("ok", True):
            raise _HostError(f"{synthetic['label']}: {step.get('error') or 'failed'}")
        return _outputs_of(step, spec)

    def expr(self, source: Any, scope: Any = None) -> Any:
        if scope is not None and not isinstance(scope, dict):
            raise _HostError("expr: scope must be an object.")
        try:
            return evaluate(str(source or ""), _runner._expression_scope(scope or {}, self.ctx))
        except ExprError as exc:
            raise _HostError(str(exc)) from None

    def template(self, value: Any, scope: Any = None) -> Any:
        if scope is not None and not isinstance(scope, dict):
            raise _HostError("template: scope must be an object.")
        return _runner._template(value, {**self.ctx, **scope} if scope else self.ctx)

    def log(self, text: str) -> None:
        live = _runner._LIVE.get()
        if not live or not self.nid:
            return
        for wid, run_id, target in (*_runner._OUTPUT_PARENTS.get(), (*live, self.nid)):
            _runner._push({"type": "workflow_output", "id": wid, "run": run_id, "node": target,
                           "source": "log", "session_id": "", "output": text})


def run_step(
    node: dict[str, Any],
    ctx: dict[str, Any],
    inputs: dict[str, Any] | None,
    *,
    code: str | None = None,
    settings: dict[str, Any] | None = None,
    dry_run: bool = False,
    calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run a code.js node: its saved code, or ``code`` (a draft). A failed step carries
    the reason; a JavaScript error also "code_error" {line, col, message}."""
    cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
    stack = _runner._CALL_STACK.get()
    wf = get_workflow(stack[-1]) if stack else None
    if wf is None:
        return {"ok": False, "error": "Custom code runs inside a saved workflow.", "result": {}}
    source = code if code is not None else str(cfg.get("code") or "")
    if not source.strip():
        source = BLANK_CODE
    decl = declared(cfg, source)
    problem = _first_error(decl["problems"])
    if problem is not None:
        line = problem.get("line")
        where = f"Line {line}: " if line else ""
        step = {"ok": False, "error": f"Fix the code first: {where}{problem.get('message') or 'error'}", "result": {}}
        step["code_error"] = {"line": line, "col": problem.get("col"), "message": str(problem.get("message") or "")}
        return step
    if dry_run:
        refused = None if _is_local(wf) else "Custom code runs in Local workflows for now."
    else:
        checked = node if code is None else {**node, "type": CODE_TYPE, "config": {**cfg, "code": source, "code_sha": decl["code_sha"]}}
        refused = _gate(wf, checked, ctx)
    if refused:
        step = {"ok": False, "error": refused, "result": {}}
        return {**step, "needs_review": True} if refused == _review_text() else step
    live = _runner._LIVE.get()
    host = _Host(node, ctx, wf, source, decl, dry_run=dry_run, calls=calls if calls is not None else [])
    out = jsrt.run(
        source,
        dict(inputs or {}),
        settings=_settings(decl.get("settings_spec") or [], cfg.get("settings") if settings is None else settings),
        nodes=ctx.get("nodes") if isinstance(ctx.get("nodes"), dict) else {},
        run_info={
            "workflow_id": str(wf.get("id") or ""),
            "workflow_name": str(wf.get("name") or ""),
            "run_id": live[1] if live else "",
            "caller_conv_id": str(ctx.get("caller_conv_id") or ""),
        },
        host=host.functions(),
        cancel_event=_runner._CANCEL.get(),
    )
    step: dict[str, Any] = {"ok": bool(out["ok"]), "result": {}, "log": out.get("log") or ""}
    if out["ok"]:
        value = out.get("value") or {}
        step["outputs"] = {pin["id"]: value.get(pin["id"]) for pin in clean_pins((decl.get("pins") or {}).get("outputs"))}
        return step
    error = out.get("error") or {}
    if out.get("stopped"):
        return {**step, "stopped": True, "error": _runner.STOPPED}
    message = str(error.get("message") or "failed")
    line = error.get("line")
    step["error"] = f"Line {line}: {message}" if line else message
    if line:
        step["code_error"] = {"line": line, "col": error.get("col"), "message": message}
    return step


def last_inputs(wf: dict[str, Any], node_id: str) -> dict[str, Any]:
    """The input values this node ran with in its latest recorded run here ({} if none)."""
    for run in reversed(list(wf.get("runs") or [])):
        for step in reversed(run.get("steps") or []) if isinstance(run, dict) else ():
            if isinstance(step, dict) and step.get("id") == node_id and isinstance(step.get("inputs"), dict):
                return dict(step["inputs"])
    return {}


def draft(
    workflow_id: str,
    node_id: str,
    code: str | None = None,
    inputs: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
    dry_run: bool = False,
    person: bool = False,
) -> dict[str, Any]:
    """runner.run_code_draft: run one node's (draft) code now, without saving or logging a run.
    Its log comes back with the result; nothing streams as a live run."""
    import time

    started = time.monotonic()

    def failed(message: str, line: Any = None, col: Any = None) -> dict[str, Any]:
        return {"ok": False, "outputs": {}, "log": "", "tool_calls": [], "error": {"message": message, "line": line, "col": col},
                "ms": int((time.monotonic() - started) * 1000)}

    wf = get_workflow(workflow_id)
    if wf is None:
        return failed("workflow not found")
    nodes = {str(n.get("id")): n for n in (wf.get("graph") or {}).get("nodes") or [] if isinstance(n, dict) and n.get("id")}
    node = nodes.get(str(node_id or ""))
    if node is None:
        return failed("That node isn't in the saved workflow; save first.")
    if str(node.get("type") or "") != CODE_TYPE:
        if code is None:
            return failed("This node has no custom code yet.")
        cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
        node = {**node, "type": CODE_TYPE, "config": {"code": code, "inputs": cfg.get("inputs") or {}, "settings": {}, "spend": cfg.get("spend") is True}}
    wid = str(wf["id"])
    ctx: dict[str, Any] = {
        "caller_conv_id": _runner._caller(""),
        "workflow_name": str(wf.get("name") or ""),
        "nodes": _runner._last_outputs(wf),
        "files": [],
    }
    flags = _runner._start_run(person, False)
    cancel = threading.Event()
    tokens = (_runner._CANCEL.set(cancel), _runner._LIVE.set(None), _runner._CALL_STACK.set((*_runner._CALL_STACK.get(), wid)))
    with _runner._ACTIVE_LOCK:
        _runner._ACTIVE.setdefault(wid, set()).add(cancel)
    calls: list[dict[str, Any]] = []
    try:
        step = run_step(node, ctx, last_inputs(wf, str(node["id"])) if inputs is None else inputs,
                        code=code, settings=settings, dry_run=dry_run, calls=calls)
    finally:
        with _runner._ACTIVE_LOCK:
            running = _runner._ACTIVE.get(wid, set())
            running.discard(cancel)
            if not running:
                _runner._ACTIVE.pop(wid, None)
        _runner._CALL_STACK.reset(tokens[2])
        _runner._LIVE.reset(tokens[1])
        _runner._CANCEL.reset(tokens[0])
        _runner._end_run(flags)
    error = None
    if not step.get("ok"):
        # The message alone: the editor puts "Line N:" in front from line and col.
        where = step.get("code_error") or {}
        error = {"message": str(where.get("message") or step.get("error") or "failed"), "line": where.get("line"), "col": where.get("col")}
    out = {"ok": bool(step.get("ok")), "outputs": step.get("outputs") or {}, "log": step.get("log") or "", "tool_calls": calls,
           "error": error, "ms": int((time.monotonic() - started) * 1000)}
    return {**out, "needs_review": True} if step.get("needs_review") else out
