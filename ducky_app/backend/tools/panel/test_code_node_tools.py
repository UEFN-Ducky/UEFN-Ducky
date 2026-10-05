"""Agent and panel tools for Custom code nodes: read, edit, test, approve."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.automations import code_approval, code_check, runner, store
from backend.automations.test_code_nodes import BLANK, install_code_api
from backend.tools.panel import panel_automations as panel
from frontend.ui_web.panel_api_automations import PanelApiAutomationsMixin


@pytest.fixture(autouse=True)
def files(monkeypatch, tmp_path):
    install_code_api(monkeypatch)
    monkeypatch.setattr(store, "use_db", lambda *_: False)
    monkeypatch.setattr(store, "_files_dir", lambda: tmp_path)
    monkeypatch.setattr(store, "_announce_graphs_changed", lambda: None)
    monkeypatch.setattr(code_approval, "use_db", lambda *_: False)
    monkeypatch.setattr(code_approval, "_approvals_file", lambda: tmp_path.parent / f"{tmp_path.name}-approvals.json")
    monkeypatch.setattr(panel, "_reveal_graph", lambda *args, **kwargs: None)
    monkeypatch.setattr(panel, "_chat_allows_everything", lambda: False)
    return tmp_path


def _workflow(**template_cfg) -> str:
    graph = {
        "nodes": [
            {"id": "s", "type": "start.manual", "x": 0, "y": 0, "config": {}},
            {"id": "a", "type": "input.text", "x": 0, "y": 200, "config": {"value": "duck"}},
            {"id": "t", "type": "text.template", "x": 300, "y": 200, "label": "Greeting",
             "config": {"template": "Hello {{name}}", "names": ["name"], **template_cfg}},
            {"id": "p", "type": "util.preview", "x": 600, "y": 200, "config": {}},
            {"id": "if", "type": "logic.if", "x": 300, "y": 0, "config": {"expression": "true"}},
        ],
        "edges": [
            {"source": "a", "target": "t", "kind": "data", "source_pin": "text", "target_pin": "name"},
            {"source": "t", "target": "p", "kind": "data", "source_pin": "text", "target_pin": "value"},
            {"source": "s", "target": "if", "kind": "main"},
        ],
    }
    return str(store.save_workflow({"name": "Greet", "graph": graph})["id"])


def _node(wid: str, nid: str) -> dict:
    return next(n for n in store.get_workflow(wid)["graph"]["nodes"] if n["id"] == nid)


def _call(tool, *args, **kwargs) -> dict:
    out = tool(*args, **kwargs)
    if asyncio.iscoroutine(out):
        out = asyncio.run(out)
    return json.loads(out)


# --------------------------------------------------------------------------- reading


def test_builtin_node_code_is_generated_with_its_pins():
    wid = _workflow()
    out = _call(panel.get_workflow_node_code, wid, "t")
    assert out["ok"] and out["kind"] == "builtin" and out["convertible"] is True
    assert '"Hello {{name}}"' in out["code"] and out["code_sha"] == code_check.code_sha(out["code"])
    assert out["pins"]["inputs"] == [{"id": "name", "label": "name", "type": "text"}]
    assert out["problems"] == [] and out["approved"] is True and out["based_on"] is None
    flow = _call(panel.get_workflow_node_code, wid, "if")
    assert flow["kind"] == "flow" and flow["convertible"] is False and flow["reason"]
    assert _call(panel.get_workflow_node_code, wid, "nope")["ok"] is False


def test_last_inputs_come_from_the_newest_recorded_step():
    wid = _workflow()
    store.append_run(wid, {"started": 1, "steps": [{"id": "t", "ok": True, "inputs": {"name": "old"}}]})
    store.append_run(wid, {"started": 2, "steps": [{"id": "t", "ok": True, "inputs": {"name": "new"}}, {"id": "p", "ok": True}]})
    assert _call(panel.get_workflow_node_code, wid, "t")["last_inputs"] == {"name": "new"}


# --------------------------------------------------------------------------- editing


def test_first_edit_converts_the_builtin_and_keeps_its_wires():
    wid = _workflow()
    read = _call(panel.get_workflow_node_code, wid, "t")
    out = _call(panel.edit_workflow_node_code, wid, "t", edits=[{"old": '"Hello {{name}}"', "new": '"Hi {{name}}!"'}],
                expected_sha=read["code_sha"])
    assert out["ok"], out
    assert out["wires_dropped"] == [] and out["problems"] == []
    node = _node(wid, "t")
    assert node["type"] == "code.js" and node["label"] == "Greeting"
    assert '"Hi {{name}}!"' in node["config"]["code"]
    assert node["config"]["based_on"] == {"type": "text.template", "config": {"template": "Hello {{name}}", "names": ["name"]},
                                          "code_sha": read["code_sha"]}
    assert node["config"]["pins"] == read["pins"] and out["pins"] == read["pins"]
    assert out["code_sha"] == node["config"]["code_sha"]
    assert len(store.get_workflow(wid)["graph"]["edges"]) == 3
    custom = _call(panel.get_workflow_node_code, wid, "t")
    assert custom["kind"] == "custom" and custom["based_on_code"] == read["code"]
    assert custom["approved"] is False  # an agent's code waits for a person


def test_edits_must_match_exactly_once_and_code_or_edits_not_both():
    wid = _workflow()
    missing = _call(panel.edit_workflow_node_code, wid, "t", edits=[{"old": "not in there", "new": "x"}])
    assert missing["ok"] is False and "isn't in the code" in missing["error"]
    twice = _call(panel.edit_workflow_node_code, wid, "t", edits=[{"old": "input", "new": "x"}])
    assert twice["ok"] is False and "times" in twice["error"]
    both = _call(panel.edit_workflow_node_code, wid, "t", code=BLANK, edits=[{"old": "a", "new": "b"}])
    assert both["ok"] is False and "not both" in both["error"]
    assert _call(panel.edit_workflow_node_code, wid, "t")["ok"] is False
    assert _node(wid, "t")["type"] == "text.template"


def test_expected_sha_mismatch_is_a_conflict():
    wid = _workflow()
    out = _call(panel.edit_workflow_node_code, wid, "t", code=BLANK, expected_sha="0" * 64)
    assert out["ok"] is False and out["conflict"] is True and "changed since you read it" in out["error"]
    assert out["code_sha"] == _call(panel.get_workflow_node_code, wid, "t")["code_sha"]


def test_wires_to_pins_that_are_gone_are_dropped_and_listed():
    wid = _workflow()
    _call(panel.edit_workflow_node_code, wid, "t", code=BLANK.replace('inputs: [{ id: "text"', 'inputs: [{ id: "name"'))
    renamed = BLANK.replace('inputs: [{ id: "text"', 'inputs: [{ id: "name"').replace('outputs: [{ id: "text"', 'outputs: [{ id: "words"')
    out = _call(panel.edit_workflow_node_code, wid, "t", code=renamed)
    assert out["ok"], out
    assert out["wires_dropped"] == [{"source": "t", "target": "p", "kind": "data", "source_pin": "text", "target_pin": "value",
                                     "reason": "no output 'text' any more"}]
    edges = store.get_workflow(wid)["graph"]["edges"]
    assert {(e["source"], e["target"]) for e in edges} == {("a", "t"), ("s", "if")}


def test_a_value_node_loses_its_white_wires_and_typed_wires_that_no_longer_fit():
    wid = _workflow()
    numbers = BLANK.replace('inputs: [{ id: "text", type: "text"', 'inputs: [{ id: "name", type: "number"'
                            ).replace('kind: "step"', 'kind: "value"')
    out = _call(panel.edit_workflow_node_code, wid, "t", code=numbers)
    assert out["ok"], out
    assert [d["reason"] for d in out["wires_dropped"]] == ["text can't feed number any more"]
    assert out["pins"]["exec"] is False


def test_revert_brings_back_the_builtin():
    wid = _workflow()
    _call(panel.edit_workflow_node_code, wid, "t", code=BLANK)
    out = _call(panel.edit_workflow_node_code, wid, "t", revert=True)
    assert out["ok"], out
    node = _node(wid, "t")
    assert node["type"] == "text.template" and node["config"] == {"template": "Hello {{name}}", "names": ["name"]}
    assert out["code_sha"] is None and out["pins"]["inputs"][0]["id"] == "name"
    again = _call(panel.edit_workflow_node_code, wid, "t", revert=True)
    assert again["ok"] is False and "built-in step already" in again["error"]


def test_route_nodes_and_locked_nodes_are_refused():
    wid = _workflow()
    route = _call(panel.edit_workflow_node_code, wid, "if", code=BLANK)
    assert route["ok"] is False and "never picks a route" in route["error"]
    wf = store.get_workflow(wid)
    graph = wf["graph"]
    next(n for n in graph["nodes"] if n["id"] == "t")["locked"] = True
    store.save_workflow({"id": wid, "graph": graph})
    held = _call(panel.edit_workflow_node_code, wid, "t", code=BLANK)
    assert held["ok"] is False and held["locked"] == ["Greeting"]
    assert _node(wid, "t")["type"] == "text.template"
    allowed = _call(panel.edit_workflow_node_code, wid, "t", code=BLANK, allow_locked_changes=True)
    assert allowed["ok"] is True


def test_edits_keep_saved_versions():
    from backend.automations.versions import list_versions

    wid = _workflow()
    before = len(list_versions(wid))
    _call(panel.edit_workflow_node_code, wid, "t", code=BLANK)
    assert len(list_versions(wid)) == before + 1


# --------------------------------------------------------------------------- approvals by who saved


def test_agent_saves_do_not_approve_unless_the_chat_allows_everything(monkeypatch):
    wid = _workflow()
    _call(panel.edit_workflow_node_code, wid, "t", code=BLANK)
    sha = code_check.code_sha(BLANK)
    assert not code_approval.is_approved(wid, "t", sha)
    graph = store.get_workflow(wid)["graph"]
    assert _call(panel.save_workflow, graph=graph, workflow_id=wid)["ok"]
    assert not code_approval.is_approved(wid, "t", sha)
    monkeypatch.setattr(panel, "_chat_allows_everything", lambda: True)
    assert _call(panel.save_workflow, graph=graph, workflow_id=wid)["ok"]
    assert code_approval.approval(wid, "t", sha)["by"] == "chat"


def test_panel_saves_and_edits_approve_as_a_person():
    wid = _workflow()
    api = PanelApiAutomationsMixin()
    out = api.edit_workflow_node_code(wid, "t", code=BLANK)
    assert out["ok"] and code_approval.approval(wid, "t", code_check.code_sha(BLANK))["by"] == "person"
    changed = BLANK.replace("Got", "Saw")
    wf = store.get_workflow(wid)
    next(n for n in wf["graph"]["nodes"] if n["id"] == "t")["config"]["code"] = changed
    assert api.save_workflow({"id": wid, "graph": wf["graph"]})["ok"]
    assert code_approval.is_approved(wid, "t", code_check.code_sha(changed))


def test_review_approves_only_the_code_on_screen():
    wid = _workflow()
    _call(panel.edit_workflow_node_code, wid, "t", code=BLANK)
    api = PanelApiAutomationsMixin()
    stale = api.approve_workflow_node_code(wid, "t", "0" * 64)
    assert stale["ok"] is False and stale["code_sha"] == code_check.code_sha(BLANK)
    assert api.approve_workflow_node_code(wid, "p", "x")["ok"] is False
    assert api.approve_workflow_node_code(wid, "t", code_check.code_sha(BLANK)) == {
        "ok": True, "approved": True, "code_sha": code_check.code_sha(BLANK)}
    assert _call(panel.get_workflow_node_code, wid, "t")["approved"] is True


def test_check_is_on_the_panel_only():
    api = PanelApiAutomationsMixin()
    out = api.check_workflow_node_code('export const node = { kind: "loop" };')
    assert out["ok"] is False and out["problems"]
    for name in ("check_workflow_node_code", "approve_workflow_node_code"):
        assert not hasattr(panel, name)


# --------------------------------------------------------------------------- get_workflow without code


def test_mcp_get_workflow_leaves_code_out_unless_asked_and_a_round_trip_keeps_it():
    wid = _workflow()
    custom = BLANK.replace("Got", "Kept")
    _call(panel.edit_workflow_node_code, wid, "t", code=custom)
    light = _call(panel.get_workflow, wid)["workflow"]
    cfg = next(n for n in light["graph"]["nodes"] if n["id"] == "t")["config"]
    assert "code" not in cfg and cfg["code_lines"] == len(custom.splitlines()) and cfg["code_sha"] == code_check.code_sha(custom)
    full = _call(panel.get_workflow, wid, include_code=True)["workflow"]
    assert next(n for n in full["graph"]["nodes"] if n["id"] == "t")["config"]["code"] == custom
    assert _call(panel.save_workflow, graph=light["graph"], workflow_id=wid, name="Renamed")["ok"]
    assert _node(wid, "t")["config"]["code"] == custom
    assert PanelApiAutomationsMixin().get_workflow(wid)["workflow"]["graph"]["nodes"][2]["config"]["code"] == custom


# --------------------------------------------------------------------------- testing and running


def test_mcp_test_runs_a_dry_draft_as_an_agent(monkeypatch):
    seen = []

    def draft(workflow_id, node_id, **kwargs):
        seen.append((workflow_id, node_id, kwargs))
        return {"ok": True, "outputs": {"text": "hi"}, "log": "", "tool_calls": [], "error": None, "ms": 3}

    monkeypatch.setattr(runner, "run_code_draft", draft, raising=False)
    out = _call(panel.test_workflow_node, "wf", "t", code="x", inputs={"text": "hi"})
    assert out["outputs"] == {"text": "hi"}
    assert seen == [("wf", "t", {"code": "x", "inputs": {"text": "hi"}, "settings": None, "dry_run": True, "person": False})]
    _call(panel.test_workflow_node, "wf", "t", dry_run=False)
    assert seen[-1][2]["dry_run"] is False and seen[-1][2]["person"] is False
    PanelApiAutomationsMixin().test_workflow_node("wf", "t", settings={"k": 1})
    assert seen[-1] == ("wf", "t", {"code": None, "inputs": None, "settings": {"k": 1}, "dry_run": False, "person": True})


def test_panel_runs_are_started_by_a_person(monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "run_workflow", lambda wid, **kw: calls.append(("run", wid, kw)) or {"ok": True})
    monkeypatch.setattr(runner, "run_node", lambda wid, nid, **kw: calls.append(("node", wid, nid, kw)) or {"ok": True})
    api = PanelApiAutomationsMixin()
    api.run_workflow("wf", payload={"x": 1})
    api.run_workflow_node("wf", "n", approve_spend=True)
    assert calls[0][2]["payload"] == {"x": 1, "_person_started": True}
    assert calls[1] == ("node", "wf", "n", {"approve_spend": True, "person": True})
    _call(panel.run_workflow_node, "wf", "n")
    assert calls[2] == ("node", "wf", "n", {})  # an agent's run never counts as a person's


# --------------------------------------------------------------------------- the API reference


def test_workflow_code_api_hands_over_types_docs_and_tool_args():
    out = _call(panel.workflow_code_api, tools=["get_workflow", "no_such_tool"])
    assert set(out) == {"ok", "dts", "manifest", "blank", "declaration_schema", "examples", "tools_dts"}
    assert 'declare module "ducky"' in out["dts"] and out["blank"] == BLANK and out["manifest"]
    assert out["declaration_schema"]["kind"]["enum"] == ["step", "value"]
    assert all(code_check.check(ex["code"])["ok"] for ex in out["examples"])
    assert '"get_workflow": { workflow_id: string; include_code?: boolean }' in out["tools_dts"]
    assert "no_such_tool: no such tool here" in out["tools_dts"]
    assert _call(panel.workflow_code_api)["tools_dts"] == ""
    assert PanelApiAutomationsMixin().workflow_code_api(["get_workflow"])["tools_dts"] == out["tools_dts"].replace(
        "    // no_such_tool: no such tool here\n", "")


def test_an_agents_run_never_counts_as_a_person_pressing_run(monkeypatch):
    """An MCP client can't approve code or spending by sending the app's own run fields."""
    seen: dict = {}
    monkeypatch.setattr(runner, "run_workflow", lambda wid, **kw: seen.update(kw) or {"ok": False, "error": "x"})
    asyncio.run(panel.run_workflow("w", payload={"_person_started": True, "_spend_approved": True, "topic": "ducks"}))
    assert seen["payload"] == {"topic": "ducks"}


def test_agent_converts_a_terminal_step_and_it_waits_for_a_person_end_to_end(monkeypatch):
    """Real runner, V8, approvals and tools: only the terminal itself is a stand-in."""
    from types import SimpleNamespace
    from backend.server import mcp

    ran: list[tuple[str, bool]] = []
    real_get = mcp._tool_manager.get_tool

    def terminal(**args):
        ran.append((args["command"], runner.typed_command_approved(args["command"])))
        return json.dumps({"ok": True, "exit_code": 0})

    monkeypatch.setattr(mcp._tool_manager, "get_tool",
                        lambda name: SimpleNamespace(fn=terminal) if name == "ducky_terminal_run" else real_get(name))
    graph = {"nodes": [
        {"id": "s", "type": "start.manual", "x": 0, "y": 0, "config": {}},
        {"id": "t", "type": "tool.call", "x": 300, "y": 0, "config": {
            "name": "ducky_terminal_run", "arguments": {"session_id": "s1", "command": "npm test"}}},
    ], "edges": [{"source": "s", "target": "t", "kind": "main"}]}
    wid = str(store.save_workflow({"name": "Build", "graph": graph})["id"])
    generated = _call(panel.get_workflow_node_code, wid, "t")
    assert generated["kind"] == "builtin" and '"npm test"' in generated["code"]

    edited = _call(panel.edit_workflow_node_code, wid, "t", code=generated["code"])
    assert edited["ok"], edited
    assert _node(wid, "t")["type"] == "code.js" and _call(panel.get_workflow_node_code, wid, "t")["approved"] is False
    refused = _call(panel.run_workflow, wid)
    assert refused["ok"] is False and "Review this code" in refused["error"] and ran == []

    assert PanelApiAutomationsMixin().run_workflow(wid)["ok"]  # a person presses Test
    assert ran == [("npm test", True)]  # written in approved code: no pop-up
    assert _call(panel.run_workflow, wid)["ok"]  # approved now, so an agent's run goes too

    changed = _call(panel.edit_workflow_node_code, wid, "t", edits=[{"old": '"npm test"', "new": '"npm run build"'}])
    assert changed["ok"], changed
    again = _call(panel.run_workflow, wid)
    assert again["ok"] is False and "Review this code" in again["error"]  # an agent's change needs a person again
    assert ran == [("npm test", True), ("npm test", True)]
