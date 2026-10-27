"""Reusable workflows: an Inputs node takes values, Return nodes give values back,
and a Run workflow node calls one like a function from a bigger workflow."""

from __future__ import annotations

import pytest

from backend.automations import runner, store


@pytest.fixture(autouse=True)
def files_store(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "use_db", lambda *_: False)
    monkeypatch.setattr(store, "_files_dir", lambda: tmp_path)
    monkeypatch.setattr(store, "_announce_graphs_changed", lambda: None)
    monkeypatch.setattr(runner, "_announce_run", lambda *a, **k: None)


def node(nid, ntype, x=0, **config):
    return {"id": nid, "type": ntype, "x": x, "y": 0, "config": config}


def wire(*pairs):
    return [{"source": a, "target": b, "kind": kind} for a, b, kind in pairs]


def greeter() -> dict:
    """Function: who (default world) -> greeting, shout."""
    return store.save_workflow({"name": "Greeter", "graph": {
        "nodes": [
            node("in", "flow.input", inputs=[{"name": "who", "default": "world"}, {"name": ""}]),
            node("out", "flow.output", 300, outputs=[{"name": "greeting", "value": "Hello {{who}}"},
                                                      {"name": "who", "value": ""}]),
        ],
        "edges": wire(("in", "out", "main")),
    }})


def caller(target: str, **args) -> dict:
    return store.save_workflow({"name": "Caller", "graph": {
        "nodes": [
            node("go", "start.manual"),
            node("call", "workflow.call", 200, workflow_id=target, args=args),
            node("check", "flow.branch", 400, field="greeting", equals="Hello Ducky"),
            node("yes", "flow.output", 600, outputs=[{"name": "said", "value": "{{returned.greeting}}"}]),
        ],
        "edges": wire(("go", "call", "main"), ("call", "check", "main"), ("check", "yes", "true")),
    }})


def test_run_workflow_passes_inputs_and_uses_what_it_returns():
    fn = greeter()
    out = runner.run_workflow(caller(fn["id"], who="{{name}}")["id"], payload={"name": "Ducky"})
    assert out["ok"] is True, out
    call = next(s for s in out["steps"] if s["type"] == "workflow.call")
    assert call["result"]["greeting"] == "Hello Ducky"
    assert [s["type"] for s in call["substeps"]] == ["flow.input", "flow.output"]
    # The Branch read the returned field; the caller's own Return gave it back.
    assert out["outputs"] == {"said": "Hello Ducky"}


def test_function_tested_alone_uses_its_defaults_and_logs_calls():
    fn = greeter()
    alone = runner.run_workflow(fn["id"])
    assert alone["outputs"] == {"greeting": "Hello world", "who": "world"}
    runner.run_workflow(caller(fn["id"], who="Ducky")["id"])
    runs = store.get_workflow(fn["id"])["runs"]
    assert runs[-1]["trigger_id"] == "workflow.call" and runs[-1]["ok"] is True


def test_blank_argument_falls_back_to_the_default_and_whole_placeholders_keep_types():
    fn = store.save_workflow({"name": "Count", "graph": {
        "nodes": [node("in", "flow.input", inputs=[{"name": "items", "default": "none"}]),
                  node("out", "flow.output", outputs=[{"name": "items", "value": ""}])],
        "edges": wire(("in", "out", "main")),
    }})
    blank = runner.run_workflow(caller(fn["id"], items="")["id"])
    assert blank["steps"][1]["result"]["items"] == "none"
    listed = runner.run_workflow(caller(fn["id"], items="{{cards}}")["id"], payload={"cards": [1, 2]})
    assert listed["steps"][1]["result"]["items"] == [1, 2]
    text = runner.run_workflow(caller(fn["id"], items="cards: {{cards}}")["id"], payload={"cards": [1, 2]})
    assert text["steps"][1]["result"]["items"] == "cards: [1, 2]"


def test_a_workflow_cannot_run_itself_even_through_another():
    a = caller("")
    b = caller(a["id"])
    store.save_workflow({"id": a["id"], "graph": caller(b["id"])["graph"]})
    out = runner.run_workflow(a["id"])
    assert out["ok"] is False and "can't run itself" in out["error"]
    assert out["error"].startswith("Caller: ")  # b's failure, named after the workflow that failed


def test_missing_or_unchosen_workflow_fails_the_node():
    assert "Choose the workflow" in runner.run_workflow(caller("")["id"])["error"]
    assert "deleted" in runner.run_workflow(caller("gone")["id"])["error"]


def test_a_failure_inside_the_called_workflow_stops_the_caller():
    broken = store.save_workflow({"name": "Broken", "graph": {
        "nodes": [node("in", "flow.input"), node("t", "tool.call", 200, name="")],
        "edges": wire(("in", "t", "main")),
    }})
    out = runner.run_workflow(caller(broken["id"])["id"])
    assert out["ok"] is False and out["error"] == "Broken: tool name required"
    assert out["steps"][-1]["substeps"][-1]["ok"] is False


def test_list_shows_functions_with_their_signature():
    fn = greeter()
    row = next(w for w in store.list_workflows() if w["id"] == fn["id"])
    assert row["trigger"] == {"kind": "function", "label": "Function"}
    assert row["signature"] == {"inputs": [{"name": "who", "default": "world"}], "outputs": ["greeting", "who"]}
    plain = caller(fn["id"])
    assert next(w for w in store.list_workflows() if w["id"] == plain["id"])["signature"]["inputs"] == []


def test_catalog_offers_the_function_nodes():
    from backend.automations.catalog import list_nodes, starter_types

    types = {n["type"]: n for n in list_nodes()}
    assert types["flow.input"]["role"] == "starter" and types["flow.output"]["role"] == "end"
    assert types["workflow.call"]["config_fields"][0]["type"] == "workflow"
    assert "flow.input" in starter_types()


def gated_part() -> dict:
    """Wait (sets waited), then only reaches Return when a score arrived."""
    return store.save_workflow({"name": "Part", "graph": {
        "nodes": [node("in", "flow.input"), node("w", "flow.wait", 200, seconds=0),
                  node("b", "flow.branch", 400, field="score", op="exists"), node("out", "flow.output", 600)],
        "edges": wire(("in", "w", "main"), ("w", "b", "main"), ("b", "out", "true")),
    }})


def outer(target: str, share: bool) -> dict:
    return store.save_workflow({"name": "Outer", "graph": {
        "nodes": [node("go", "start.manual"), node("call", "workflow.call", 200, workflow_id=target, share=share),
                  node("end", "flow.output", 400, outputs=[{"name": "waited"}, {"name": "score"}])],
        "edges": wire(("go", "call", "main"), ("call", "end", "main")),
    }})


def test_shared_run_sees_every_field_and_hands_them_all_back():
    shared = outer(gated_part()["id"], share=True)
    out = runner.run_workflow(shared["id"], payload={"score": 5})
    assert out["ok"] is True and out["outputs"] == {"waited": 0.0, "score": 5}


def test_no_return_reached_stops_the_callers_path():
    part = gated_part()
    stopped = runner.run_workflow(outer(part["id"], share=True)["id"])  # no score: the Branch goes false
    assert stopped["ok"] is True and stopped["outputs"] == {}
    assert [s["type"] for s in stopped["steps"]] == ["start.manual", "workflow.call"]
    # Without sharing, the called workflow never sees the caller's score.
    assert runner.run_workflow(outer(part["id"], share=False)["id"], payload={"score": 5})["outputs"] == {}


# Oct 26 2026: a release's "Run until it passes" failed all 3 tries, ended at Return to
# user, and every workflow above it reported OK with its commit and go-live skipped.

def replies(monkeypatch, answers: list) -> list:
    calls: list = []

    def call(cfg, payload, node_id=""):
        calls.append(1)
        answer = answers[min(len(calls), len(answers)) - 1]
        return answer if isinstance(answer, dict) else {"ok": True, "result": {"text": answer}}

    monkeypatch.setattr(runner, "_call_tool", call)
    return calls


def looper(until: str = 'text.includes("PASS")', tries: int = 3) -> dict:
    """Repeat a Run step until it passes, then Return to user (its Return is never reached)."""
    return store.save_workflow({"name": "Looper", "graph": {
        "nodes": [node("in", "flow.input"), node("r", "flow.repeat", 200, max=tries, expression=until),
                  {**node("t", "tool.call", 400, name="x"), "label": "Run"},
                  node("fin", "pipeline.finish", 600, message="FAILED after {{attempts}} tries"),
                  node("out", "flow.output", 800)],
        "edges": wire(("in", "r", "main"), ("r", "t", "each"), ("r", "fin", "done")),
    }})


def test_a_called_workflow_that_ended_failed_fails_the_caller(monkeypatch):
    replies(monkeypatch, [{"ok": False, "error": "command not approved (timed out)"}])
    fn = looper()
    out = runner.run_workflow(caller(fn["id"])["id"])
    assert out["ok"] is False and out["error"] == "Looper: Run: command not approved (timed out)"
    assert [s["type"] for s in out["steps"]] == ["start.manual", "workflow.call"]  # nothing after the call
    run = store.get_workflow(fn["id"])["runs"][-1]
    assert run["ok"] is False and run["error"] == "Run: command not approved (timed out)"


def test_a_check_that_never_said_yes_fails_the_caller(monkeypatch):
    replies(monkeypatch, ["FAIL"])
    out = runner.run_workflow(caller(looper()["id"])["id"])
    assert out["ok"] is False and out["error"] == "Looper: Repeat until: not passed after 3 tries"


def test_failed_once_then_passed_is_still_a_quiet_stop(monkeypatch):
    replies(monkeypatch, [{"ok": False, "error": "busy"}, "PASS"])
    out = runner.run_workflow(caller(looper()["id"])["id"])
    assert out["ok"] is True and [s["type"] for s in out["steps"]] == ["start.manual", "workflow.call"]


def test_repeat_n_times_with_no_check_is_a_quiet_stop(monkeypatch):
    calls = replies(monkeypatch, ["anything"])
    out = runner.run_workflow(caller(looper(until="", tries=2)["id"])["id"])
    assert out["ok"] is True and len(calls) == 2


def test_nested_failure_names_every_workflow(monkeypatch):
    replies(monkeypatch, [{"ok": False, "error": "command not approved (timed out)"}])
    middle = store.save_workflow({"name": "Middle", "graph": {
        "nodes": [node("in", "flow.input"), node("call", "workflow.call", 200, workflow_id=looper()["id"]),
                  node("out", "flow.output", 400)],
        "edges": wire(("in", "call", "main"), ("call", "out", "main")),
    }})
    out = runner.run_workflow(caller(middle["id"])["id"])
    assert out["ok"] is False and out["error"] == "Middle: Looper: Run: command not approved (timed out)"
