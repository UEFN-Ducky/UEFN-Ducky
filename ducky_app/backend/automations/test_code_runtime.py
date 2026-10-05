"""Custom code nodes ("code.js"): the V8 sandbox, the ducky calls and the runner around them.

Everything here runs real JavaScript in mini-racer; only the approvals store, the code
checker and MCP tools are stand-ins (they live in other modules)."""

from __future__ import annotations

import contextvars
import hashlib
import sys
import threading
import time
import types
from types import SimpleNamespace

import pytest

import backend.automations as automations_pkg
from backend.automations import code_api, code_node, jsrt, plugin, runner, store
from backend.automations.pins import node_pins


def _module(body: str, *, decl: str = "{}") -> str:
    return f"// @ts-check\nexport const node = {decl};\n\nexport default async function run(input, ducky) {{\n{body}\n}}\n"


def _run(code: str, input: dict | None = None, *, host: dict | None = None, cancel: threading.Event | None = None, **kw):
    return jsrt.run(code, input or {}, settings=kw.pop("settings", {}), nodes=kw.pop("nodes", {}), run_info=kw.pop("run_info", {}),
                    host=host or {}, cancel_event=cancel, **kw)


# --------------------------------------------------------------------------- the sandbox


def test_host_call_returns_value_and_log():
    seen = []
    host = {"tool": lambda name, args: {"echo": name, "n": args["n"] + 1}, "log": seen.append}
    out = _run(_module('  const r = await ducky.tool("t", { n: 1 });\n  ducky.log("got", r);\n  return { n: r.n, s: ducky.settings.k };'),
               host=host, settings={"k": "v"})
    assert out["ok"], out
    assert out["value"] == {"n": 2, "s": "v"}
    assert out["log"] == 'got {"echo":"t","n":2}'
    assert seen and seen[-1] == out["log"]  # streamed to the node while it runs


def test_promise_all_runs_host_calls_together():
    def slow(name, args):
        time.sleep(0.3)
        return args["i"]

    started = time.time()
    out = _run(_module('  const r = await Promise.all([1, 2, 3, 4].map((i) => ducky.tool("t", { i })));\n  return { r };'),
               host={"tool": slow})
    assert out["ok"], out
    assert out["value"] == {"r": [1, 2, 3, 4]}
    assert time.time() - started < 1.0  # four 0.3 s calls side by side, not one after another


def test_thrown_error_names_its_line():
    code = _module("  const a = 1;\n  if (a) {\n    throw new Error(\"boom\");\n  }\n  return {};")
    out = _run(code)
    assert out["ok"] is False
    assert out["error"]["message"] == "boom"
    assert out["error"]["line"] == 7  # the throw, counting the two declaration lines and the blank one
    assert out["error"]["col"] == 11
    type_error = _run(_module("  const x = null;\n  return { y: x.z };"))
    assert type_error["error"]["line"] == 6 and type_error["error"]["message"].startswith("TypeError:")
    top_level = _run("export const node = {};\nnull.boom;\nexport default async function run() { return {}; }\n")
    assert top_level["error"]["line"] == 2


def test_error_after_a_host_call_names_the_users_line():
    def tool(name, args):
        if name == "missing":
            raise jsrt.HostError(f"{name} refused")
        return {}

    code = _module('  await ducky.tool("t", {});\n  const r = await ducky.tool("missing", {});\n  return {};')
    out = _run(code, host={"tool": tool})
    assert out["error"]["message"] == "missing refused"
    assert out["error"]["line"] == 6


@pytest.mark.parametrize("body", [
    "  while (true) {}",
    '  await ducky.tool("t", {});\n  while (true) {}',
    '  ducky.log("busy");\n  await ducky.expr("1");\n  for (;;) { Math.random(); }',
])
def test_runaway_code_stops_within_two_seconds(body):
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    started = time.time()
    out = _run(_module(body), host={"tool": lambda *_: {}, "expr": lambda *_: 1}, cancel=cancel)
    assert out["ok"] is False and out["stopped"] is True
    assert time.time() - started < 2.0
    deadline = time.time() + 2
    while [t for t in threading.enumerate() if t.name == "custom-code"]:  # the isolate gets closed
        assert time.time() < deadline
        time.sleep(0.02)


def test_memory_cap_ends_the_run():
    code = _module("  const keep = [];\n  while (true) keep.push(new Array(100000).fill(1.5));")
    out = _run(code, memory_mb=64)
    assert out["ok"] is False
    assert "Out of memory" in out["error"]["message"]


def test_code_generation_from_strings_is_off():
    for body in ('  return { v: eval("1 + 1") };', '  return { v: new Function("return 1")() };',
                 '  return { v: (async function () {}).constructor("return 1") };'):
        out = _run(_module(body))
        assert out["ok"] is False, body
        assert "Code generation from strings disallowed" in out["error"]["message"]


def test_nothing_outside_ducky_is_reachable():
    body = ('  return { kinds: [typeof require, typeof fetch, typeof process, typeof WebAssembly, typeof setTimeout,\n'
            '    typeof Atomics, typeof SharedArrayBuffer, typeof XMLHttpRequest, typeof importScripts],\n'
            '    frozen: Object.isFrozen(ducky) && Object.isFrozen(ducky.settings) && Object.isFrozen(ducky.run) };')
    out = _run(_module(body), settings={"a": {"b": 1}})
    assert out["ok"], out
    assert out["value"]["kinds"] == ["undefined"] * 9
    assert out["value"]["frozen"] is True
    tamper = _run(_module('  ducky.tool = () => 1;\n  return {};'))
    assert tamper["ok"] is False and "read only" in tamper["error"]["message"]


def test_python_tracebacks_and_paths_never_reach_the_code():
    def broken(name, args):
        raise RuntimeError("could not open C:\\Users\\someone\\AppData\\Local\\secret\\thing.json")

    out = _run(_module('  try { await ducky.tool("t", {}); } catch (e) { return { m: e.message }; }'), host={"tool": broken})
    message = out["value"]["m"]
    assert "Traceback" not in message and "someone" not in message and "AppData" not in message
    assert "thing.json" in message
    missing = _run(_module('  try { await ducky.builtin("x", {}); } catch (e) { return { m: e.message }; }'))
    assert missing["value"]["m"] == "ducky.builtin isn't available"


def test_host_calls_see_the_callers_context():
    marker: contextvars.ContextVar[str] = contextvars.ContextVar("marker", default="")
    token = marker.set("from the step")
    try:
        out = _run(_module('  return { seen: await ducky.tool("t", {}) };'), host={"tool": lambda *_: marker.get()})
    finally:
        marker.reset(token)
    assert out["value"] == {"seen": "from the step"}


def test_result_must_be_a_small_object():
    assert _run(_module("  return 5;"))["error"]["message"].startswith("run must return an object")
    assert _run(_module("  return undefined;"))["value"] == {}
    big = _run(_module('  return { s: "x".repeat(1100000) };'))
    assert big["ok"] is False and "1 MB" in big["error"]["message"]


def test_sleep_ends_early_when_stopped():
    cancel = threading.Event()
    threading.Timer(0.2, cancel.set).start()
    started = time.time()
    out = _run(_module("  await ducky.sleep(30);\n  return {};"), cancel=cancel)
    assert out["stopped"] is True and time.time() - started < 2.0


def test_check_syntax_reports_line_and_column_without_running():
    problems = jsrt.check_syntax(_module("  let a = ;\n  return {};"))
    assert problems == [{"line": 5, "col": 11, "message": "SyntaxError: Unexpected token ';'", "severity": "error"}]
    assert jsrt.check_syntax(_module("  while (true) {}")) == []  # parsed, never run
    assert jsrt.check_syntax(code_api.BLANK_CODE) == []
    assert jsrt.check_syntax("export const node = {};\nexport function other() {}\n")[0]["line"] == 2


def test_exports_are_blanked_in_place():
    code = "export const node = {};\n  export default async function run(i, d) {}\n"
    stripped = jsrt.strip_exports(code)
    assert stripped == "       const node = {};\n                 async function run(i, d) {}\n"


def test_blank_code_runs_and_matches_its_pins():
    out = _run(code_api.BLANK_CODE, {"text": "hi"})
    assert out["ok"] and out["value"] == {"text": "hi"} and out["log"] == "Got hi"
    assert node_pins({"type": "code.js", "config": {"pins": code_api.BLANK_PINS}}, None) == {
        "exec": True,
        "inputs": [{"id": "text", "label": "Text", "type": "text"}],
        "outputs": [{"id": "text", "label": "Text", "type": "text"}],
    }


def test_code_api_types_cover_the_manifest():
    dts = code_api.dts()
    assert dts.startswith('declare module "ducky" {') and "export interface Ducky" in dts
    for entry in code_api.MANIFEST:
        assert entry["ts"] in dts and set(entry) == {"name", "ts", "doc", "async"}
    assert "code.js" in code_api.FLOW_TYPES and "tool.call" not in code_api.FLOW_TYPES


def test_literal_strings_skip_comments_and_templates():
    code = 'const a = "npm run build"; // "rm -rf x"\nconst b = \'it\\\'s\'; const c = `plain`; const d = `x ${a}`;\n/* "hidden" */'
    found = code_node.literal_strings(code)
    assert {"npm run build", "it's", "plain"} <= found
    assert "rm -rf x" not in found and "hidden" not in found and "x ${a}" not in found


# --------------------------------------------------------------------------- in workflows


def _sha(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _code_node(nid: str, code: str, *, inputs=(), outputs=(), step: bool = True, tools=(), builtins=(), **config) -> dict:
    pins = {"exec": step,
            "inputs": [{"id": pin, "label": pin, "type": "any"} for pin in inputs],
            "outputs": [{"id": pin, "label": pin, "type": "any"} for pin in outputs]}
    cfg = {"code": code, "code_sha": _sha(code), "pins": pins, "settings_spec": [], "problems": [],
           "uses": {"tools": list(tools), "builtins": list(builtins)}, "settings": {}, "inputs": {}, "spend": False, **config}
    return {"id": nid, "type": "code.js", "label": nid, "x": 0, "y": 0, "config": cfg}


@pytest.fixture()
def events(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "use_db", lambda *_: False)
    monkeypatch.setattr(store, "_files_dir", lambda: tmp_path)
    monkeypatch.setattr(store, "_announce_graphs_changed", lambda: None)
    seen: list[dict] = []
    monkeypatch.setattr("frontend.ui_web.agent_modes.push_ui_event", seen.append)
    return seen


@pytest.fixture()
def approvals(monkeypatch):
    """The per-PC approvals store, as code_approval behaves."""
    fake = types.ModuleType("backend.automations.code_approval")
    fake.approved = {}
    fake.gate_calls = []

    def approve(workflow_id, node_id, code_sha, by):
        fake.approved[(workflow_id, node_id, code_sha)] = by

    def is_approved(workflow_id, node_id, code_sha):
        return (workflow_id, node_id, code_sha) in fake.approved

    def gate(wf, node, ctx):
        fake.gate_calls.append((node["id"], node["config"].get("code_sha")))
        if (wf.get("owner") or {}).get("kind", "local") != "local":
            return "Custom code runs in Local workflows for now."
        key = (wf["id"], node["id"], node["config"]["code_sha"])
        if key in fake.approved:
            return None
        if ctx.get("_person_started"):
            approve(*key, "person")
            return None
        return "Review this code before it runs: it was changed by an agent. Open the node and press Review, or run it once yourself."

    fake.approve, fake.is_approved, fake.gate = approve, is_approved, gate
    monkeypatch.setitem(sys.modules, "backend.automations.code_approval", fake)
    monkeypatch.setattr(automations_pkg, "code_approval", fake, raising=False)
    return fake


def _save(nodes: list[dict], edges: list[dict]) -> str:
    return str(store.save_workflow({"name": "Code", "graph": {"nodes": nodes, "edges": edges}})["id"])


def _main(a: str, b: str) -> dict:
    return {"source": a, "target": b, "kind": "main"}


def _data(a: str, pin_a: str, b: str, pin_b: str) -> dict:
    return {"source": a, "target": b, "kind": "data", "source_pin": pin_a, "target_pin": pin_b}


def test_code_in_a_workflow_as_a_step_and_a_value_node(events, approvals):
    value = _code_node("v", _module("  return { n: 21 };"), outputs=["n"], step=False)
    step = _code_node("s", _module('  ducky.log("doubling", input.n);\n  return { doubled: input.n * 2, stray: 1 };'),
                      inputs=["n"], outputs=["doubled"])
    after = _code_node("t", _module('  return { flat: await ducky.template("{{doubled}}"), wired: await ducky.template("{{nodes.s.doubled}}"),\n'
                                    '    seen: ducky.nodes.s, run: ducky.run.workflow_name };'),
                       outputs=["flat", "wired", "seen", "run"])
    wid = _save([{"id": "go", "type": "start.manual", "config": {}}, value, step, after],
                [_main("go", "s"), _main("s", "t"), _data("v", "n", "s", "n")])
    out = runner.run_workflow(wid, payload={"_person_started": True})
    assert out["ok"], out
    assert out["node_outputs"]["v"] == {"n": 21}
    assert out["node_outputs"]["s"] == {"doubled": 42}  # only the declared outputs
    assert out["node_outputs"]["t"] == {"flat": None, "wired": 42, "seen": {"doubled": 42}, "run": "Code"}
    steps = {s.get("id"): s for s in out["steps"]}
    assert steps["s"]["inputs"] == {"n": 21}  # what it ran with, on its record
    assert steps["s"]["log"] == "doubling 21"
    assert "doubled" not in steps["s"].get("result", {})
    logs = [e for e in events if e["type"] == "workflow_output" and e.get("source") == "log"]
    assert logs and logs[-1]["node"] == "s" and logs[-1]["output"] == "doubling 21"
    assert node_pins(step, None)["exec"] is True and node_pins(value, None)["exec"] is False


def test_code_waits_for_review_until_a_person_runs_it(events, approvals):
    node = _code_node("c", _module("  return { ok: 1 };"), outputs=["ok"])
    wid = _save([{"id": "go", "type": "start.manual", "config": {}}, node], [_main("go", "c")])
    refused = runner.run_workflow(wid)
    assert refused["ok"] is False and "Review this code" in refused["error"]
    assert runner.run_workflow(wid, payload={"_person_started": True}, caller_conv_id="chat-1")["ok"] is False  # a chat's ducky is not a person
    assert runner.run_workflow(wid, payload={"_person_started": True})["ok"] is True
    assert runner.run_workflow(wid)["ok"] is True  # approved now: it runs unattended
    assert runner.run_node(wid, "c")["ok"] is True


def test_run_node_as_a_person_approves(events, approvals):
    node = _code_node("c", _module("  return { n: input.n };"), inputs=["n"], outputs=["n"])
    node["config"]["inputs"] = {"n": 7}
    wid = _save([node], [])
    assert runner.run_node(wid, "c")["ok"] is False
    done = runner.run_node(wid, "c", person=True)
    assert done["ok"], done
    assert done["node_outputs"]["c"] == {"n": 7}
    assert done["steps"][0]["inputs"] == {"n": 7}


def test_code_with_problems_does_not_run(events, approvals):
    node = _code_node("c", _module("  return {};"), problems=[{"line": 5, "col": 3, "message": "Unexpected token", "severity": "error"}])
    wid = _save([node], [])
    out = runner.run_node(wid, "c", person=True)
    assert out["ok"] is False and "Fix the code first: Line 5: Unexpected token" in out["error"]
    assert approvals.gate_calls == []


def test_team_workflows_refuse_code(events, approvals, monkeypatch):
    wid = _save([_code_node("c", _module("  return {};"))], [])
    real = store.get_workflow
    monkeypatch.setattr(code_node, "get_workflow", lambda w: {**real(w), "owner": {"kind": "team", "id": "t1"}})
    out = runner.run_node(wid, "c", person=True)
    assert out["ok"] is False and out["error"].endswith("Custom code runs in Local workflows for now.")


def test_a_thrown_error_fails_the_step_with_its_line(events, approvals):
    wid = _save([_code_node("c", _module("  const a = 1;\n  throw new Error(`bad ${a}`);"))], [])
    out = runner.run_node(wid, "c", person=True)
    assert out["ok"] is False and out["error"] == "c: Line 6: bad 1"
    assert out["steps"][0]["code_error"] == {"line": 6, "col": 9, "message": "bad 1"}


@pytest.fixture()
def tools(monkeypatch):
    """Stand-in MCP tools: each call is recorded with whether its command skipped the pop-up."""
    from backend.server import mcp

    calls: list[dict] = []
    behaviour: dict = {}

    def make(name):
        def fn(**arguments):
            calls.append({"name": name, "args": arguments, "typed": runner.typed_command_approved(str(arguments.get("command") or ""))})
            if name in behaviour:
                return behaviour[name](**arguments)
            return '{"ok": true, "exit_code": 0}'
        return SimpleNamespace(fn=fn)

    monkeypatch.setattr(mcp._tool_manager, "get_tool", make)
    return SimpleNamespace(calls=calls, behaviour=behaviour)


def test_tools_must_be_declared_and_lose_spend(events, approvals, tools):
    code = _module('  const r = await ducky.tool("ducky_terminal_run", { session_id: "s1", command: "npm test", spend: true, confirm_spend: true });\n'
                   '  let refused = "";\n  try { await ducky.tool("other_tool", {}); } catch (e) { refused = e.message; }\n'
                   '  return { exit: r.exit_code, refused };')
    wid = _save([_code_node("c", code, outputs=["exit", "refused"], tools=["ducky_terminal_run"])], [])
    out = runner.run_node(wid, "c", person=True)
    assert out["ok"], out
    assert out["node_outputs"]["c"] == {"exit": 0, "refused": "other_tool isn't in node.tools: add it there to call it."}
    assert tools.calls == [{"name": "ducky_terminal_run", "args": {"session_id": "s1", "command": "npm test"}, "typed": True}]


def test_failed_tool_result_throws(events, approvals, tools):
    tools.behaviour["t"] = lambda **_: {"ok": False, "error": "nope"}
    wid = _save([_code_node("c", _module('  await ducky.tool("t", {});\n  return {};'), tools=["t"])], [])
    out = runner.run_node(wid, "c", person=True)
    assert out["error"] == "c: Line 5: nope"


def test_typed_command_skips_the_popup_only_when_approved_and_written_in_the_code(events, approvals, tools, monkeypatch):
    code = _module('  const base = "npm";\n'
                   '  await ducky.tool("ducky_terminal_run", { session_id: "s", command: "npm run build" });\n'
                   '  await ducky.tool("ducky_terminal_run", { session_id: "s", command: base + " publish" });\n'
                   '  return {};')
    wid = _save([_code_node("c", code, tools=["ducky_terminal_run"])], [])
    assert runner.run_node(wid, "c", person=True)["ok"]
    assert [(c["args"]["command"], c["typed"]) for c in tools.calls] == [("npm run build", True), ("npm publish", False)]
    tools.calls.clear()
    # Allowed to run (a gate that says yes) but never approved: the pop-up still asks.
    monkeypatch.setattr(approvals, "gate", lambda wf, node, ctx: None)
    approvals.approved.clear()
    assert runner.run_node(wid, "c")["ok"]
    assert [c["typed"] for c in tools.calls] == [False, False]


def test_builtin_runs_listed_nodes_and_refuses_flow_nodes(events, approvals):
    code = _module('  const t = await ducky.builtin("text.template", { a: "duck" }, { template: "Hi {{a}}", names: [{ name: "a" }] });\n'
                   '  let flow = "";\n  try { await ducky.builtin("flow.end", {}); } catch (e) { flow = e.message; }\n'
                   '  let unlisted = "";\n  try { await ducky.builtin("list.count", { list: [1] }); } catch (e) { unlisted = e.message; }\n'
                   '  return { text: t.text, flow, unlisted, value: await ducky.expr("a * 2", { a: 21 }) };')
    wid = _save([_code_node("c", code, outputs=["text", "flow", "unlisted", "value"], builtins=["text.template", "flow.end"])], [])
    out = runner.run_node(wid, "c", person=True)
    assert out["ok"], out
    assert out["node_outputs"]["c"] == {"text": "Hi duck", "flow": "flow.end steers the run, so code can't run it.",
                                        "unlisted": "list.count isn't in node.builtins: add it there to run it.", "value": 42}


@pytest.mark.parametrize("node_spend, approved_by_play, expected", [(False, False, None), (True, False, True), (False, True, True)])
def test_builtin_spends_only_with_the_nodes_switch_or_a_person(events, approvals, monkeypatch, node_spend, approved_by_play, expected):
    seen = []
    monkeypatch.setattr(plugin, "get_handler", lambda ntype: (lambda ctx: seen.append(ctx["config"]) or {"made": 1}) if ntype == "test.paid" else None)
    monkeypatch.setattr("backend.automations.media.BACKENDS", {"test.paid": {}}, raising=False)
    monkeypatch.setattr(runner.media, "run_media", lambda ntype, cfg, values, folder: (seen.append(cfg) or {"ok": True, "outputs": {"made": 1}}))
    code = _module('  return await ducky.builtin("test.paid", {}, { spend: true, confirm_spend: true, size: 2 });')
    wid = _save([_code_node("c", code, outputs=["made"], builtins=["test.paid"], spend=node_spend)], [])
    out = runner.run_node(wid, "c", person=True, approve_spend=approved_by_play)
    assert out["ok"], out
    assert seen[-1].get("spend") is expected and seen[-1]["size"] == 2 and "confirm_spend" not in seen[-1]


def test_stop_during_a_host_call_ends_the_run(events, approvals, tools):
    release = threading.Event()
    tools.behaviour["slow"] = lambda **_: release.wait(30) and '{"ok": true}'
    wid = _save([{"id": "go", "type": "start.manual", "config": {}},
                 _code_node("c", _module('  await ducky.tool("slow", {});\n  return {};'), tools=["slow"])], [_main("go", "c")])
    result: dict = {}
    worker = threading.Thread(target=lambda: result.update(runner.run_workflow(wid, payload={"_person_started": True})))
    worker.start()
    deadline = time.time() + 5
    while not tools.calls:
        assert time.time() < deadline
        time.sleep(0.02)
    started = time.time()
    assert runner.stop_workflow(wid) is True
    worker.join(5)
    release.set()
    assert not worker.is_alive() and time.time() - started < 2.0
    assert result["ok"] is False and result["error"] == runner.STOPPED


@pytest.fixture()
def checker(monkeypatch):
    """code_check as the save path has it: the declaration read in Python, plus syntax."""
    import json
    import re

    fake = types.ModuleType("backend.automations.code_check")

    def check(code):
        match = re.search(r"export const node = (\{.*?\});\n", code, re.S)
        decl = json.loads(match.group(1)) if match else {}
        problems = jsrt.check_syntax(code)
        pins = {"exec": decl.get("kind", "step") == "step", "inputs": decl.get("inputs", []), "outputs": decl.get("outputs", [])}
        return {"ok": not problems, "problems": problems, "node": decl, "pins": pins, "settings_spec": decl.get("settings", []),
                "uses": {"tools": decl.get("tools", []), "builtins": decl.get("builtins", [])}, "code_sha": _sha(code)}

    fake.check = check
    monkeypatch.setitem(sys.modules, "backend.automations.code_check", fake)
    monkeypatch.setattr(automations_pkg, "code_check", fake, raising=False)
    return fake


def test_draft_dry_run_lists_calls_without_making_them(events, approvals, tools, checker):
    wid = _save([_code_node("c", _module("  return {};"))], [])
    decl = '{"kind": "step", "inputs": [{"id": "q", "type": "text"}], "outputs": [{"id": "r", "type": "any"}], ' \
           '"settings": [{"id": "who", "label": "Who", "type": "text", "default": "duck"}], "tools": ["ducky_terminal_run"], "builtins": ["text.template"]}'
    draft = _module('  const r = await ducky.tool("ducky_terminal_run", { session_id: "s", command: "rm -rf /", spend: true });\n'
                    '  const b = await ducky.builtin("text.template", { a: 1 }, { template: "{{a}}" });\n'
                    '  ducky.log(ducky.settings.who, input.q);\n  return { r: [r, b] };', decl=decl)
    out = runner.run_code_draft(wid, "c", code=draft, inputs={"q": "hi"}, dry_run=True)
    assert out["ok"], out
    assert out["outputs"] == {"r": [{"ok": True, "dry_run": True}, {"ok": True, "dry_run": True}]}
    assert out["log"] == "duck hi"
    assert out["tool_calls"] == [
        {"name": "ducky_terminal_run", "args": {"session_id": "s", "command": "rm -rf /"}, "dry_run": True},
        {"name": "text.template", "args": {"input": {"a": 1}, "config": {"template": "{{a}}"}}, "dry_run": True, "builtin": True},
    ]
    assert tools.calls == [] and approvals.approved == {}  # nothing ran, nothing approved
    assert store.get_workflow(wid)["graph"]["nodes"][0]["config"]["code"] != draft  # not saved
    assert out["error"] is None and isinstance(out["ms"], int)


def test_draft_real_run_needs_review_and_reuses_last_inputs(events, approvals, checker):
    node = _code_node("c", _module("  return { n: input.n };"), inputs=["n"], outputs=["n"])
    node["config"]["inputs"] = {"n": 3}
    wid = _save([node], [])
    assert runner.run_node(wid, "c", person=True)["ok"]
    draft = _module("  return { n: input.n * 10 };", decl='{"inputs": [{"id": "n", "type": "number"}], "outputs": [{"id": "n", "type": "number"}]}')
    refused = runner.run_code_draft(wid, "c", code=draft)
    assert refused["ok"] is False and "Review this code" in refused["error"]["message"]
    done = runner.run_code_draft(wid, "c", code=draft, person=True)
    assert done["ok"], done
    assert done["outputs"] == {"n": 30}  # inputs from its last run
    broken = runner.run_code_draft(wid, "c", code=_module("  return {;"), person=True)
    assert broken["ok"] is False and broken["error"]["line"] == 5 and broken["error"]["message"].startswith("Fix the code first: Line 5")


def test_every_step_records_its_inputs(events):
    wid = _save([
        {"id": "a", "type": "input.text", "config": {"value": "x" * 50000}},
        {"id": "t", "type": "text.template", "config": {"template": "{{a}}", "names": [{"name": "a"}]}},
        {"id": "p", "type": "util.preview", "config": {}},
    ], [_data("a", "text", "t", "a"), _data("t", "text", "p", "value")])
    out = runner.run_workflow(wid)
    steps = {s.get("id"): s for s in out["steps"]}
    assert steps["a"]["inputs"] == {}
    assert steps["t"]["inputs"]["a"].startswith("xxx") and len(steps["t"]["inputs"]["a"]) <= 4001
    assert len(str(steps["p"]["inputs"])) < 17000


def test_catalog_and_runner_know_custom_code():
    from backend.automations import catalog

    spec = catalog.node_specs()["code.js"]
    assert spec["label"] == "Custom code" and spec["group"] == "Code" and spec["role"] == "action"
    assert spec["default_config"]["code"] == code_api.BLANK_CODE and spec["default_config"]["pins"] == code_api.BLANK_PINS
    assert "code.js" in runner._ACTION_TYPES
    assert runner.public_payload({"_person_started": True, "_spend_approved": True, "x": 1}) == {"x": 1}
