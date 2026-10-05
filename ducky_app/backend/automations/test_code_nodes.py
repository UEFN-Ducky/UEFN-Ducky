"""Custom code nodes: the declaration check, saving, approvals and the generated code."""

from __future__ import annotations

import hashlib
import json
import sys
import time
import types

import pytest

from backend.automations import code_approval, code_check, codegen, store
from backend.automations.pins import node_pins

BLANK = """// @ts-check
export const node = {
  kind: "step",
  inputs: [{ id: "text", type: "text", label: "Text" }],
  outputs: [{ id: "text", type: "text", label: "Text" }],
  settings: [],
  tools: [],
  builtins: [],
};

/** @param {Record<string, any>} input @param {import("ducky").Ducky} ducky */
export default async function run(input, ducky) {
  ducky.log("Got", input.text);
  return { text: input.text };
}
"""

FLOW = frozenset({"start.manual", "start.cron", "start.chat", "spotlight.step", "spotlight.closed", "flow.input", "flow.output",
                  "flow.end", "flow.foreach", "flow.repeat", "flow.branch", "logic.if", "workflow.call", "fortnite.servers",
                  "pipeline.finish", "util.preview", "code.js"})


def install_code_api(monkeypatch) -> None:
    """The ducky API module comes with the runtime; stand in for it where it isn't built yet."""
    try:
        import backend.automations.code_api  # noqa: F401
        return
    except ImportError:
        pass
    fake = types.ModuleType("backend.automations.code_api")
    fake.BLANK_CODE = BLANK
    fake.FLOW_TYPES = FLOW
    fake.MANIFEST = [{"name": "tool", "ts": "tool(name: string, args?: object): Promise<any>", "doc": "Call an MCP tool.", "async": True}]
    fake.dts = lambda: 'declare module "ducky" { export interface Ducky { tool(name: string, args?: object): Promise<any>; } }'
    monkeypatch.setitem(sys.modules, "backend.automations.code_api", fake)
    import backend.automations as package

    monkeypatch.setattr(package, "code_api", fake, raising=False)


@pytest.fixture(autouse=True)
def code_api(monkeypatch):
    install_code_api(monkeypatch)


@pytest.fixture()
def files(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "use_db", lambda *_: False)
    monkeypatch.setattr(store, "_files_dir", lambda: tmp_path)
    monkeypatch.setattr(store, "_announce_graphs_changed", lambda: None)
    monkeypatch.setattr(code_approval, "use_db", lambda *_: False)
    monkeypatch.setattr(code_approval, "_approvals_file", lambda: tmp_path.parent / f"{tmp_path.name}-approvals.json")
    return tmp_path


def _code(decl: str, body: str = "  return {};") -> str:
    return f"export const node = {decl};\n\nexport default async function run(input, ducky) {{\n{body}\n}}\n"


def _errors(result: dict) -> list[tuple[int, int, str]]:
    return [(p["line"], p["col"], p["message"]) for p in result["problems"] if p["severity"] == "error"]


# --------------------------------------------------------------------------- the check


def test_blank_code_passes_and_declares_its_pins():
    out = code_check.check(BLANK)
    assert out["ok"] is True and out["problems"] == []
    assert out["pins"] == {"exec": True, "inputs": [{"id": "text", "label": "Text", "type": "text"}],
                           "outputs": [{"id": "text", "label": "Text", "type": "text"}]}
    assert out["settings_spec"] == [] and out["uses"] == {"tools": [], "builtins": []}
    assert out["code_sha"] == hashlib.sha256(BLANK.encode()).hexdigest()


def test_declaration_reads_comments_quotes_bare_keys_and_trailing_commas():
    code = _code("""{
  // a value node
  'kind': 'value', /* no white pins */
  inputs: [{ id: 'list', "type": "json", label: "It's a list", required: true, },],
  outputs: [{ id: "count", type: "number", default: -1.5e2 }],
  settings: [
    { id: "mode", type: "select", label: "Mode", options: ["a", { id: "b", label: "B" }], default: "a" },
    { id: "limit", type: "number", default: 3 },
  ],
  tools: ["ducky_terminal_run", "ducky_terminal_run"],
  builtins: ["image.resize"],
}""")
    out = code_check.check(code)
    assert out["ok"] is True, out["problems"]
    assert out["pins"]["exec"] is False
    assert out["pins"]["inputs"] == [{"id": "list", "label": "It's a list", "type": "json", "required": True}]
    assert out["pins"]["outputs"] == [{"id": "count", "label": "count", "type": "number", "default": -150.0}]
    assert out["settings_spec"] == [
        {"id": "mode", "label": "Mode", "type": "select", "default": "a", "options": [{"id": "a", "label": "a"}, {"id": "b", "label": "B"}]},
        {"id": "limit", "label": "limit", "type": "number", "default": 3},
    ]
    assert out["uses"] == {"tools": ["ducky_terminal_run"], "builtins": ["image.resize"]}


@pytest.mark.parametrize("decl, line, col, words", [
    ('{\n  kind: "step",\n  inputs: someList,\n}', 3, 11, "someList is a name"),
    ('{\n  kind: `step`,\n}', 2, 9, "template literal"),
    ('{\n  ...base,\n}', 2, 3, "spread"),
    ('{\n  kind: "step\n}', 2, 9, "isn't closed"),
    ('{\n  kind: "step",\n  kind: "value",\n}', 3, 3, "appears twice"),
    ('{\n  inputs: [1 + 2],\n}', 2, 14, "Expected , or ]"),
    ('{ kind: "step" } + extra', 1, 38, "nothing after it"),
])
def test_declaration_errors_point_at_line_and_column(decl, line, col, words):
    out = code_check.check(_code(decl))
    assert out["ok"] is False
    found = [e for e in _errors(out) if words in e[2]]
    assert found and found[0][:2] == (line, col), out["problems"]


def test_declaration_rules_for_pins_settings_and_builtins():
    out = code_check.check(_code("""{
  kind: "loop",
  inputs: [{ id: "a", type: "texty" }, { id: "a", type: "text" }, { type: "text" }],
  settings: [{ id: "s", type: "dropdown" }],
  builtins: ["logic.if"],
  colour: "red",
}"""))
    messages = " | ".join(p["message"] for p in out["problems"])
    assert out["ok"] is False
    for words in ('kind is "step"', "a: type is one of", "inputs has 'a' twice", "Each of inputs needs an id",
                  "s: type is one of", "logic.if chooses where a workflow goes", "node has no 'colour'"):
        assert words in messages
    assert next(p for p in out["problems"] if "colour" in p["message"])["severity"] == "warning"


def test_missing_parts_and_extra_exports():
    assert "Declare the node" in _errors(code_check.check("export default async function run() {}\n"))[0][2]
    out = code_check.check('export const node = { kind: "step" };\nexport function helper() {}\n')
    messages = [e[2] for e in _errors(out)]
    assert any("Add the run function" in m for m in messages)
    assert (2, 1, "Only node and run are exported; remove this export.") in _errors(out)


@pytest.mark.parametrize("snippet, words, at", [
    ('const fs = require("fs");', "require()", "require"),
    ('await fetch("https://example.com");', "fetch()", "fetch"),
    ('import("x");', "import", "import"),
    ('eval("1");', "eval()", "eval"),
    ('const f = new Function("return 1");', "new Function", "new"),
    ('const f = Function("return 1");', "Function()", "Function"),
    ("ducky.log(process.env);", "process", "process"),
    ("WebAssembly.compile(x);", "WebAssembly", "WebAssembly"),
])
def test_banned_constructs_are_errors_at_their_line(snippet, words, at):
    out = code_check.check(_code('{ kind: "step" }', f"  const x = 1;\n  {snippet}\n  return {{}};"))
    hits = [e for e in _errors(out) if words in e[2]]
    assert len(hits) == 1 and hits[0][:2] == (5, 3 + snippet.index(at)), out["problems"]


def test_banned_words_in_strings_comments_and_regexes_are_fine():
    body = ('  // fetch(url) would need the network; require() too\n'
            '  ducky.log("no fetch( here", \'nor eval(\', `nor ${"import"} here`);\n'
            '  const re = /process\\.env/;\n'
            '  /* new Function */\n'
            '  return { a: 1 / 2 };')
    out = code_check.check(_code('{ kind: "step" }', body))
    assert out["ok"] is True, out["problems"]


def test_code_inside_template_substitutions_is_still_checked():
    out = code_check.check(_code('{ kind: "step" }', "  ducky.log(`x ${fetch(\"u\")} y`);\n  return {};"))
    assert any("fetch()" in e[2] for e in _errors(out))


def test_size_cap_is_an_error():
    big = BLANK + "//" + "x" * (code_check.MAX_NODE_BYTES + 10) + "\n"
    out = code_check.check(big)
    assert out["ok"] is False
    assert "a node holds at most 64 KB" in out["problems"][0]["message"]


def test_js_syntax_comes_from_the_engine_when_it_is_there(monkeypatch):
    calls = []

    def check_syntax(code):
        calls.append(code)
        return [{"line": 12, "col": 9, "message": "Unexpected token ';'", "severity": "error"}]

    fake = types.ModuleType("backend.automations.jsrt")
    fake.check_syntax = check_syntax
    monkeypatch.setitem(sys.modules, "backend.automations.jsrt", fake)
    import backend.automations as package

    monkeypatch.setattr(package, "jsrt", fake, raising=False)
    out = code_check.check(BLANK)
    assert calls == [BLANK]
    assert out["ok"] is False and _errors(out) == [(12, 9, "Unexpected token ';'")]
    assert code_check.first_error(out["problems"]) == "Line 12: Unexpected token ';'"


def test_check_skips_js_syntax_without_the_engine(monkeypatch):
    monkeypatch.setitem(sys.modules, "backend.automations.jsrt", None)  # import fails
    assert code_check.check(BLANK)["ok"] is True


# --------------------------------------------------------------------------- saving


def _graph(cfg: dict, *, extra_nodes: list | None = None) -> dict:
    return {"nodes": [{"id": "s", "type": "start.manual", "config": {}},
                      {"id": "c", "type": "code.js", "label": "Mine", "config": cfg}, *(extra_nodes or [])],
            "edges": [{"source": "s", "target": "c", "kind": "main"}]}


def _code_node(wf: dict, nid: str = "c") -> dict:
    return next(n for n in wf["graph"]["nodes"] if n["id"] == nid)


def test_save_fills_blank_code_sha_problems_and_pins(files):
    wf = store.save_workflow({"name": "Code", "graph": _graph({})})
    cfg = _code_node(wf)["config"]
    assert cfg["code"] == BLANK
    assert cfg["code_sha"] == code_check.code_sha(BLANK)
    assert cfg["problems"] == []
    assert cfg["pins"]["inputs"][0]["id"] == "text"
    assert cfg["settings_spec"] == [] and cfg["uses"] == {"tools": [], "builtins": []}
    assert cfg["settings"] == {} and cfg["inputs"] == {} and cfg["spend"] is False
    assert store.get_workflow(wf["id"])["graph"]["nodes"][1]["config"]["code"] == BLANK


def test_broken_code_saves_with_problems_and_keeps_the_last_good_pins(files):
    good = BLANK.replace('outputs: [{ id: "text"', 'outputs: [{ id: "words"')
    wf = store.save_workflow({"name": "Code", "graph": _graph({"code": good})})
    assert _code_node(wf)["config"]["pins"]["outputs"][0]["id"] == "words"
    broken = good.replace('outputs: [{ id: "words", type: "text"', 'outputs: [{ id: "other", type: oops')
    wf = store.save_workflow({"id": wf["id"], "graph": _graph({"code": broken})})
    cfg = _code_node(wf)["config"]
    assert cfg["code"] == broken
    assert cfg["problems"] and cfg["problems"][0]["severity"] == "error"
    assert cfg["pins"]["outputs"][0]["id"] == "words"  # wires to the old pins stay
    assert cfg["code_sha"] == code_check.code_sha(broken)


def test_graph_sent_back_without_code_keeps_it_while_the_sha_matches(files):
    custom = BLANK.replace('"Got"', '"Kept"')
    wf = store.save_workflow({"name": "Code", "graph": _graph({"code": custom})})
    sha = _code_node(wf)["config"]["code_sha"]
    wf = store.save_workflow({"id": wf["id"], "graph": _graph({"code_sha": sha, "code_lines": 9, "settings": {"x": 1}})})
    cfg = _code_node(wf)["config"]
    assert cfg["code"] == custom and cfg["code_sha"] == sha and cfg["settings"] == {"x": 1}
    with pytest.raises(ValueError, match="came without its code"):
        store.save_workflow({"id": wf["id"], "graph": _graph({"code_sha": "0" * 64})})


def test_size_caps_refuse_the_save(files):
    huge = BLANK + "//" + "x" * code_check.MAX_NODE_BYTES + "\n"
    with pytest.raises(ValueError, match="a node holds at most 64 KB"):
        store.save_workflow({"name": "Big", "graph": _graph({"code": huge})})
    chunk = BLANK + "//" + "x" * (60 * 1024) + "\n"
    nodes = [{"id": f"k{n}", "type": "code.js", "config": {"code": chunk}} for n in range(4)]
    with pytest.raises(ValueError, match="the most is 256 KB"):
        store.save_workflow({"name": "Many", "graph": _graph({"code": chunk}, extra_nodes=nodes)})


def test_team_workflows_refuse_code_nodes():
    with pytest.raises(ValueError, match="Custom code runs in Local workflows for now"):
        store._with_code(store.normalize_graph(_graph({"code": BLANK})), None, team=True)
    plain = store.normalize_graph({"nodes": [{"id": "s", "type": "start.manual"}], "edges": []})
    assert store._with_code(plain, None, team=True) is plain


# --------------------------------------------------------------------------- approvals


def _local(cfg_code: str = BLANK, *, kind: str = "local") -> tuple[dict, dict]:
    node = {"id": "c", "type": "code.js", "config": {"code": cfg_code}}
    return {"id": "wf1", "owner": {"kind": kind}, "graph": {"nodes": [node]}}, node


def test_gate_refuses_team_and_unreviewed_agent_code(files, monkeypatch):
    monkeypatch.setattr("backend.tools.panel.permission_prompt.allows_everything", lambda conv: False)
    wf, node = _local(kind="team")
    assert code_approval.gate(wf, node, {"_person_started": True}) == code_approval.TEAM_REFUSAL
    wf, node = _local()
    assert code_approval.gate(wf, node, {"caller_conv_id": "chat-1"}) == code_approval.REVIEW
    assert "Review this code before it runs" in code_approval.REVIEW


def test_a_person_starting_the_run_approves_that_exact_code(files):
    wf, node = _local()
    assert code_approval.gate(wf, node, {"_person_started": True}) is None
    assert code_approval.approval("wf1", "c", code_check.code_sha(BLANK))["by"] == "person"
    assert code_approval.gate(wf, node, {}) is None  # approved now: runs unattended
    node["config"]["code"] = BLANK.replace("Got", "Changed")
    assert code_approval.gate(wf, node, {}) == code_approval.REVIEW  # a new version asks again


def test_a_chat_that_allows_everything_approves(files, monkeypatch):
    asked = []
    monkeypatch.setattr("backend.tools.panel.permission_prompt.allows_everything", lambda conv: asked.append(conv) or conv == "trusted")
    wf, node = _local()
    assert code_approval.gate(wf, node, {"caller_conv_id": "other"}) == code_approval.REVIEW
    assert code_approval.gate(wf, node, {"caller_conv_id": "trusted"}) is None
    assert asked == ["other", "trusted"]
    assert code_approval.approval("wf1", "c", code_check.code_sha(BLANK))["by"] == "chat"


def test_approvals_survive_and_stay_per_node(files):
    code_approval.approve("wf1", "a", "sha-a", "person")
    code_approval.approve("wf1", "b", "sha-b", "chat")
    assert code_approval.is_approved("wf1", "a", "sha-a")
    assert not code_approval.is_approved("wf1", "a", "sha-b")
    assert not code_approval.is_approved("wf2", "a", "sha-a")
    for n in range(12):
        code_approval.approve("wf1", "a", f"v{n}", "person")
        time.sleep(0.001)
    assert code_approval.is_approved("wf1", "a", "v11") and not code_approval.is_approved("wf1", "a", "sha-a")
    code_approval.forget("wf1")
    assert not code_approval.is_approved("wf1", "b", "sha-b")


def test_approvals_use_the_local_database_when_it_is_on(monkeypatch):
    rows: dict[str, object] = {}
    monkeypatch.setattr(code_approval, "use_db", lambda table: table == "workspace_state")
    monkeypatch.setattr("backend.store.repos.kv.get_doc", lambda table, key: rows.get(f"{table}/{key}"))
    monkeypatch.setattr("backend.store.repos.kv.set_doc", lambda table, key, value: rows.__setitem__(f"{table}/{key}", json.loads(json.dumps(value))))
    monkeypatch.setattr("backend.store.repos.kv.delete_doc", lambda table, key: rows.pop(f"{table}/{key}", None))
    code_approval.approve("wf9", "n", "abc", "person")
    assert list(rows) == ["workspace_state/code_approval:wf9"]
    assert code_approval.is_approved("wf9", "n", "abc")


def test_deleting_a_workflow_forgets_its_approvals(files):
    wf = store.save_workflow({"name": "Code", "graph": _graph({})})
    assert code_approval.approve_workflow(wf, "person") == ["c"]
    assert code_approval.is_approved(wf["id"], "c", code_check.code_sha(BLANK))
    store.delete_workflow(wf["id"])
    assert not code_approval.is_approved(wf["id"], "c", code_check.code_sha(BLANK))


# --------------------------------------------------------------------------- generated code


_SAMPLE_CONFIGS = {
    "tool.call": {"name": "ducky_terminal_run", "arguments_json": '{"command": "npm run build", "session_id": "{{terminal}}"}'},
    "input.text": {"value": "hello `there` ${x}"},
    "input.json": {"value": '{"a": [1, 2]}'},
    "input.number": {"value": "4.5"},
    "input.images": {"value": [{"path": "C:/x/a.png"}]},
    "text.template": {"template": "Hi {{a}}", "names": ["a", "b"]},
    "logic.expression": {"expression": "a * 2", "names": ["a"]},
    "list.make": {"names": ["x", "y", "z"]},
    "llm.extract": {"names": ["title", "first name"]},
    "image.generate": {"backend": "gemini", "spend": True, "inputs": {"prompt": "a duck"}},
    "notify.message": {"on_fail": True},
}


def _all_nodes(specs: dict) -> list[dict]:
    nodes = [{"id": "n1", "type": t, "config": {}} for t in specs]
    nodes += [{"id": "n2", "type": t, "config": cfg} for t, cfg in _SAMPLE_CONFIGS.items()]
    return nodes


def test_every_catalog_type_generates_checked_code_with_its_exact_pins():
    from backend.automations.catalog import node_specs

    specs = node_specs()
    assert len(specs) > 50
    for node in _all_nodes(specs):
        ntype = node["type"]
        made = codegen.generate(node, specs)
        assert set(made) == {"code", "kind", "convertible", "reason"}
        if ntype in FLOW or made["kind"] == "flow":
            assert ntype in FLOW and made["convertible"] is False and made["reason"]
            assert all(line.startswith("//") or not line for line in made["code"].splitlines())
            continue
        checked = code_check.check(made["code"])
        assert checked["ok"] is True, (ntype, checked["problems"])
        assert checked["pins"] == node_pins(node, specs[ntype]), ntype
        assert made["convertible"] is True
        if made["kind"] == "host":
            assert checked["uses"] == {"tools": [], "builtins": [ntype]}, ntype
        else:
            assert checked["uses"]["builtins"] == [], ntype


def test_real_js_only_where_it_is_exact():
    from backend.automations.catalog import node_specs

    specs = node_specs()
    kinds = {t: codegen.generate({"id": "n", "type": t, "config": _SAMPLE_CONFIGS.get(t, {})}, specs)["kind"] for t in specs}
    for ntype in ("tool.call", "input.text", "input.json", "text.template", "logic.expression", "list.make", "list.get", "list.count", "list.join"):
        assert kinds[ntype] == "real", ntype
    for ntype in ("logic.compare", "list.filter", "list.map", "llm.ask", "image.generate", "pipeline.agent", "flow.wait"):
        assert kinds[ntype] == "host", ntype
    tool = codegen.generate({"id": "n", "type": "tool.call", "config": _SAMPLE_CONFIGS["tool.call"]}, specs)
    assert 'ducky.tool("ducky_terminal_run", args)' in tool["code"] and '"npm run build"' in tool["code"]
    assert code_check.check(tool["code"])["uses"]["tools"] == ["ducky_terminal_run"]
    # A Call tool that reads its tool or arguments from the run keeps its built-in.
    assert codegen.generate({"id": "n", "type": "tool.call", "config": {}}, specs)["kind"] == "host"
    assert codegen.generate({"id": "n", "type": "input.json", "config": {"value": "{oops"}}, specs)["kind"] == "host"


def test_generated_code_states_what_the_built_in_did_that_code_does_not():
    from backend.automations.catalog import node_specs

    specs = node_specs()
    notify = codegen.generate({"id": "n", "type": "notify.message", "config": {"on_fail": True}}, specs)
    assert "fails before it" in notify["reason"] and "fails before it" in notify["code"]
    assert "{{nodes.<id>.<pin>}}" in notify["reason"]
    paid = codegen.generate({"id": "n", "type": "image.generate", "config": _SAMPLE_CONFIGS["image.generate"]}, specs)
    assert "Spend switch" in paid["reason"]
    assert '"spend"' not in paid["code"] and "spend:" not in paid["code"] and "a duck" not in paid["code"]
    assert 'backend: "gemini"' in paid["code"]
    flow = codegen.generate({"id": "n", "type": "logic.if", "config": {"expression": "score > 10"}}, specs)
    assert flow["kind"] == "flow" and "score > 10" in flow["code"]


def test_plugin_nodes_generate_too(monkeypatch):
    from backend.automations import catalog

    contrib = {
        "automations_nodes": [{"id": "demo.resize", "label": "Demo", "plugin_id": "demo",
                               "inputs": [{"id": "image", "type": "image", "required": True}], "outputs": [{"id": "image", "type": "image"}]}],
        "automations_triggers": [{"id": "demo.fired", "label": "Fired", "plugin_id": "demo"}],
    }
    monkeypatch.setattr(catalog, "_contributions", lambda: (contrib, {"demo"}))
    specs = catalog.node_specs()
    made = codegen.generate({"id": "p", "type": "demo.resize", "config": {"x": 1}}, specs)
    checked = code_check.check(made["code"])
    assert made["kind"] == "host" and checked["ok"] and checked["uses"]["builtins"] == ["demo.resize"]
    assert checked["pins"] == node_pins({"type": "demo.resize"}, specs["demo.resize"])
    trigger = codegen.generate({"id": "t", "type": "demo.fired", "config": {}}, specs)
    assert trigger["kind"] == "flow" and trigger["convertible"] is False
    off = codegen.generate({"id": "o", "type": "gone.plugin", "config": {}}, specs)
    assert off["kind"] == "flow" and "plugin" in off["reason"]


def test_examples_pass_the_check():
    for example in codegen.EXAMPLES:
        assert code_check.check(example["code"])["ok"] is True, example["title"]


def _v8():
    try:
        from py_mini_racer import MiniRacer
    except ImportError:
        pytest.skip("mini-racer isn't installed")
    return MiniRacer


def _module(code: str) -> str:
    return code.replace("export const node", "const node", 1).replace("export default ", "", 1)


def test_generated_code_compiles_in_v8():
    from backend.automations.catalog import node_specs

    MiniRacer = _v8()
    specs = node_specs()
    sources = [made["code"] for node in _all_nodes(specs) if (made := codegen.generate(node, specs))["kind"] != "flow"]
    sources += [example["code"] for example in codegen.EXAMPLES]
    for source in sources:
        ctx = MiniRacer()
        try:
            assert ctx.eval(_module(source) + "\n;typeof run") == "function"
        finally:
            ctx.close()


@pytest.mark.parametrize("ntype, cfg, inputs", [
    ("list.get", {}, {"list": "[1, 2, 3]", "index": -1}),
    ("list.get", {}, {"list": [1], "index": 1.5}),
    ("list.get", {"index": 1}, {"list": ["a", "b"]}),
    ("list.get", {}, {"list": []}),
    ("list.count", {}, {"list": "héllo"}),
    ("list.count", {}, {"list": {"a": 1, "b": 2}}),
    ("list.join", {"separator": "\\n"}, {"list": [1, True, None, {"a": 1}]}),
    ("list.join", {}, {"list": "[\"x\", \"y\"]"}),
    ("list.make", {"names": ["a", "b"]}, {"a": 1, "b": None}),
    ("list.make", {}, {"b": 2, "a": 1}),
])
def test_real_list_code_does_what_the_built_in_does(ntype, cfg, inputs):
    from backend.automations import listops
    from backend.automations.catalog import node_specs

    MiniRacer = _v8()
    try:
        expected = listops.HANDLERS[ntype](cfg, inputs, {})["outputs"]
    except ValueError as exc:
        expected = {"error": str(exc)}
    made = codegen.generate({"id": "n", "type": ntype, "config": cfg}, node_specs())
    ctx = MiniRacer()
    try:
        ctx.eval(_module(made["code"]))
        promise = ctx.eval(f"run({json.dumps(inputs)}, {{}}).then((v) => JSON.stringify(v), (e) => JSON.stringify({{ error: e.message }}))")
        got = json.loads(promise.get(timeout=5))
    finally:
        ctx.close()
    assert got == expected
