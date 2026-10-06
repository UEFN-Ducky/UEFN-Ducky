"""Typed pins: data wires carry values between nodes; data nodes run when pulled."""

import pytest

from backend.automations import runner, store
from backend.automations.pins import accepts, node_pins


@pytest.fixture(autouse=True)
def files(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "use_db", lambda *_: False)
    monkeypatch.setattr(store, "_files_dir", lambda: tmp_path)
    monkeypatch.setattr(store, "_announce_graphs_changed", lambda: None)
    monkeypatch.setattr("frontend.ui_web.agent_modes.push_ui_event", lambda _e: None)


def node(nid, ntype, x=0, **config):
    return {"id": nid, "type": ntype, "x": x, "y": 0, "config": config}


def wire(source, source_pin, target, target_pin):
    return {"source": source, "target": target, "kind": "data", "source_pin": source_pin, "target_pin": target_pin}


def save(nodes, edges, name="Flow"):
    return str(store.save_workflow({"name": name, "graph": {"nodes": nodes, "edges": edges}})["id"])


def test_a_graph_without_a_start_runs_as_dataflow():
    wid = save(
        [node("t", "input.text", value="Ducky"), node("n", "input.number", value="3"),
         node("tpl", "text.template", template="{{name}} x{{count}}", names=["name", "count"]),
         node("p", "util.preview")],
        [wire("t", "text", "tpl", "name"), wire("n", "number", "tpl", "count"), wire("tpl", "text", "p", "value")],
    )
    out = runner.run_workflow(wid)
    assert out["ok"] is True, out
    assert out["node_outputs"]["p"] == {"value": "Ducky x3"}
    assert out["node_outputs"]["n"] == {"number": 3}
    assert [step["id"] for step in out["steps"]] == ["t", "n", "tpl", "p"]  # pulled in the order needed


def test_if_routes_on_a_condition_written_against_wired_values():
    def run(value):
        wid = save(
            [node("s", "start.manual"), node("num", "input.number", value=value),
             node("if", "logic.if", expression="value > 10 && value < 100"),
             node("big", "flow.end"), node("small", "flow.end")],
            [{"source": "s", "target": "if", "kind": "main"}, wire("num", "number", "if", "value"),
             {"source": "if", "target": "big", "kind": "true"}, {"source": "if", "target": "small", "kind": "false"}],
        )
        return runner.run_workflow(wid)

    assert [step["id"] for step in run(12)["steps"]] == ["s", "num", "if", "big"]
    assert [step["id"] for step in run(5)["steps"]] == ["s", "num", "if", "small"]
    bad = save([node("s", "start.manual"), node("if", "logic.if", expression="value >")], [{"source": "s", "target": "if", "kind": "main"}])
    out = runner.run_workflow(bad)
    assert out["ok"] is False and "ends too early" in out["error"]


def test_run_workflow_passes_pin_values_both_ways():
    child = save(
        [node("in", "flow.input", inputs=[{"name": "n", "type": "number"}]),
         node("x", "logic.expression", expression="a * 2", names=["a"]),
         node("ret", "flow.output", outputs=[{"name": "doubled", "type": "number"}])],
        [{"source": "in", "target": "ret", "kind": "main"}, wire("in", "n", "x", "a"), wire("x", "result", "ret", "doubled")],
        name="Double",
    )
    sig = store.signature_of(store.get_workflow(child)["graph"]["nodes"])
    assert sig == {"inputs": [{"name": "n", "default": "", "type": "number"}], "outputs": ["doubled"], "output_types": {"doubled": "number"}}
    parent = save(
        [node("s", "start.manual"), node("five", "input.number", value=5),
         node("call", "workflow.call", workflow_id=child), node("show", "util.preview")],
        [{"source": "s", "target": "call", "kind": "main"}, wire("five", "number", "call", "n"), wire("call", "doubled", "show", "value")],
    )
    pins = node_pins(store.get_workflow(parent)["graph"]["nodes"][2], {}, lambda wid: store.signature_of(store.get_workflow(wid)["graph"]["nodes"]))
    assert [pin["id"] for pin in pins["inputs"]] == ["n"] and pins["outputs"] == [{"id": "doubled", "label": "doubled", "type": "number"}]
    out = runner.run_workflow(parent)
    assert out["ok"] is True, out
    assert out["node_outputs"]["show"] == {"value": 10}


def test_a_data_loop_fails_with_a_clear_message():
    wid = save(
        [node("a", "logic.expression", expression="b + 1", names=["b"]), node("b", "logic.expression", expression="a + 1", names=["a"]),
         node("p", "util.preview")],
        [wire("a", "result", "b", "a"), wire("b", "result", "a", "b"), wire("a", "result", "p", "value")],
    )
    out = runner.run_workflow(wid)
    assert out["ok"] is False and "feeds itself" in out["error"]


def test_reading_a_step_that_has_not_run_gives_nothing_and_a_note():
    wid = save(
        [node("s", "start.manual"), node("t", "tool.call", name="nope"), node("p", "util.preview")],
        [wire("t", "result", "p", "value")],  # Call tool never runs: nothing leads to it
    )
    out = runner.run_workflow(wid)
    assert out["node_outputs"]["p"] == {"value": None}
    assert any("before it ran" in step.get("warning", "") for step in out["steps"])


def test_ask_a_model_uses_the_picked_model(monkeypatch):
    seen = {}

    def fake(provider, prompt, model="", *, system="", images=None):
        seen.update(provider=provider, prompt=prompt, model=model, system=system)
        return {"ok": True, "text": "A rubber duck."}

    monkeypatch.setattr("backend.automations.llm_complete.complete_prompt", fake)
    monkeypatch.setattr("backend.agent.model_pricing.resolve_provider_for_model", lambda model, recorded="": "anthropic")
    wid = save(
        [node("q", "input.text", value="What floats?"), node("ask", "llm.ask", model="anthropic:claude-x", system="Answer briefly."),
         node("p", "util.preview")],
        [wire("q", "text", "ask", "prompt"), wire("ask", "text", "p", "value")],
    )
    out = runner.run_workflow(wid)
    assert out["ok"] is True, out
    assert out["node_outputs"]["p"] == {"value": "A rubber duck."}
    # Instructions are the system prompt, never mixed into the wired text.
    assert seen == {"provider": "anthropic", "prompt": "What floats?", "model": "claude-x", "system": "Answer briefly."}


@pytest.mark.parametrize("op, a, b, expected", [
    ("equals", 5, "5", True), ("not_equals", 5, 6, True), ("greater", 7, 3, True), ("less", "a", "b", True),
    ("contains", "rubber duck", "duck", True), ("starts", "duckling", "duck", True), ("matches", "E42", r"^E\d+$", True),
    ("empty", "", None, True), ("empty", [1], None, False),
])
def test_compare_without_code(op, a, b, expected):
    assert runner._compare_node({"op": op}, {"a": a, "b": b}, {})["outputs"]["result"] is expected


def test_wires_keep_their_pins_and_one_per_input():
    graph = store.normalize_graph({
        "nodes": [node("a", "input.text"), node("b", "input.text"), node("p", "util.preview")],
        "edges": [wire("a", "text", "p", "value"), wire("b", "text", "p", "value"), {"source": "a", "target": "p", "kind": "data"}],
    })
    assert graph["edges"] == [wire("a", "text", "p", "value")]


@pytest.mark.parametrize("target, source, ok", [
    ("text", "text", True), ("text", "number", True), ("number", "text", False), ("any", "image", True),
    ("images", "image", True), ("image", "images", False), ("file", "audio", True), ("audio", "video", False),
    ("json", "mesh", True), ("boolean", "any", True),
])
def test_which_wires_fit(target, source, ok):
    assert accepts(target, source) is ok


def test_input_nodes_hand_on_files_as_file_refs():
    wid = save([node("img", "input.image", value=[{"path": "C:/art/duck.png"}]), node("p", "util.preview")], [wire("img", "image", "p", "value")])
    out = runner.run_workflow(wid)
    got = out["node_outputs"]["p"]["value"]
    assert {key: got[key] for key in ("kind", "path", "name")} == {"kind": "image", "path": "C:/art/duck.png", "name": "duck.png"}
    assert got["url"].startswith("http://127.0.0.1:4199/workflow-media/") and got["url"].endswith("/duck.png")  # the editor's thumbnail link


def test_wired_values_reach_a_plugin_node(monkeypatch):
    from backend.automations import catalog, plugin

    seen = []

    def handler(ctx):
        seen.append(ctx)
        return {"ok": True, "name": ctx["inputs"]["card"]["name"]}

    specs = catalog.node_specs()
    spec = {"type": "test.card_step", "label": "Card step", "role": "action", "plugin_id": "testplug",
            "inputs": [{"id": "card", "label": "Card", "type": "json"}, {"id": "note", "label": "Note", "type": "text"}],
            "outputs": [{"id": "name", "label": "Name", "type": "text"}]}
    monkeypatch.setattr(catalog, "node_specs", lambda: {**specs, "test.card_step": spec})
    monkeypatch.setattr("backend.uefn_plugins.host.is_plugin_enabled", lambda pid: pid == "testplug")
    plugin.register_node("testplug", "test.card_step", handler)
    try:
        wid = save(
            [node("s", "start.manual"), node("card", "input.json", value='{"name": "Duck Knight"}'),
             node("step", "test.card_step", mode="x", inputs={"note": "hello {{who}}"}), node("p", "util.preview")],
            [{"source": "s", "target": "step", "kind": "main"}, wire("card", "value", "step", "card"), wire("step", "name", "p", "value")],
        )
        out = runner.run_workflow(wid, payload={"who": "duck"})
    finally:
        plugin.clear_for_plugin("testplug")
    assert out["ok"] is True, out
    ctx = seen[0]
    assert ctx["inputs"] == {"card": {"name": "Duck Knight"}, "note": "hello duck"}
    assert ctx["config"]["mode"] == "x" and ctx["node"]["id"] == "step" and ctx["kind"] == "automation"
    assert {"payload", "files", "artifact_dir"} <= set(ctx)
    assert out["node_outputs"]["p"] == {"value": "Duck Knight"}
