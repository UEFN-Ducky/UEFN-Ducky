"""Workflow MCP tools open the editor, refuse unknown owners, and manage templates."""

from __future__ import annotations

import json
import asyncio
import contextvars
import threading


def test_workflow_runner_keeps_mcp_loop_responsive(monkeypatch):
    from backend.tools.panel import panel_automations as panel

    caller = contextvars.ContextVar("test_workflow_caller")
    monkeypatch.setattr(panel, "_reveal_graph", lambda *args, **kwargs: None)

    async def check():
        loop = asyncio.get_running_loop()
        loop_thread = threading.get_ident()
        caller.set("calling-chat")

        async def diagnostic():
            return "diagnostics returned"

        def runner(*args, **kwargs):
            assert threading.get_ident() != loop_thread
            assert caller.get() == "calling-chat"
            # Reproduce the worker agent requesting tools from the same loop.
            result = asyncio.run_coroutine_threadsafe(diagnostic(), loop).result(timeout=2)
            return {"ok": True, "evidence": result}

        monkeypatch.setattr("backend.automations.runner.run_workflow", runner)
        monkeypatch.setattr("backend.automations.runner.run_node", runner)
        for request in (panel.run_workflow("demo", caller_conv_id="calling-chat", wait=True),
                        panel.run_workflow_node("demo", "build")):
            result = json.loads(await request)
            assert result["ok"] is True
            assert result["evidence"] == "diagnostics returned"

    asyncio.run(check())


def _events(monkeypatch) -> list[dict]:
    events: list[dict] = []
    monkeypatch.setattr("frontend.ui_web.agent_modes.push_ui_event", events.append)
    return events


def test_save_workflow_opens_editor_in_local(monkeypatch):
    from backend.automations.store import delete_workflow
    from backend.tools.panel.panel_automations import save_workflow

    events = _events(monkeypatch)
    raw = save_workflow(
        name="Live",
        graph={"nodes": [{"id": "s", "type": "start.chat", "x": 0, "y": 0, "config": {}}], "edges": []},
    )
    doc = json.loads(raw)
    wid = doc["workflow"]["id"]
    try:
        assert doc["ok"] is True
        assert doc["workflow"]["owner"]["kind"] == "local"
        # The editor opens on it and frames what was added.
        assert {"type": "graph_focus", "id": wid, "action": "saved", "nodes": ["s"]} in events
    finally:
        delete_workflow(wid)


def test_delete_workflow_asks_the_user_first_then_opens_editor(monkeypatch):
    from backend.automations.store import get_workflow
    from backend.tools.panel.panel_automations import delete_workflow, save_workflow

    events = _events(monkeypatch)
    wid = json.loads(save_workflow(name="Gone", graph={"nodes": [], "edges": []}))["workflow"]["id"]
    events.clear()
    refused = json.loads(delete_workflow(wid))
    assert refused["ok"] is False and refused["needs_confirmation"] is True
    assert "ducky_ask_user" in refused["error"] and "“Gone”" in refused["error"]
    assert get_workflow(wid) is not None and events == []  # nothing deleted without the user's yes
    out = json.loads(delete_workflow(wid, confirmed=True))
    assert out == {"ok": True, "id": wid}
    assert {"type": "graph_focus", "id": wid, "action": "deleted"} in events
    assert json.loads(delete_workflow(wid, confirmed=True))["ok"] is False
    assert json.loads(delete_workflow(wid))["error"] == "workflow not found"


def test_delete_workflow_needs_no_second_yes_when_the_chat_allows_everything(monkeypatch):
    from backend.automations.store import get_workflow
    from backend.tools.panel import panel_automations
    from backend.tools.panel.panel_automations import delete_workflow, save_workflow

    _events(monkeypatch)
    wid = json.loads(save_workflow(name="Gone too", graph={"nodes": [], "edges": []}))["workflow"]["id"]
    monkeypatch.setattr(panel_automations, "_chat_allows_everything", lambda: True)
    assert json.loads(delete_workflow(wid)) == {"ok": True, "id": wid}
    assert get_workflow(wid) is None


def test_find_workflows_then_make_one_from_a_template(monkeypatch):
    from backend.automations.store import delete_workflow as _delete
    from backend.tools.panel.panel_automations import create_workflow_from_template, find_workflows, save_workflow

    events = _events(monkeypatch)
    mine = json.loads(save_workflow(name="Chest prop to UEFN", description="Makes a 3D chest and imports it", graph={"nodes": [], "edges": []}))["workflow"]["id"]
    try:
        found = json.loads(find_workflows("make a 3D chest model for UEFN"))
        assert found["ok"] and found["workflows"][0]["id"] == mine and "run_workflow" in found["hint"]
        assert found["templates"][0]["id"] == "builtin:pipe-prompt-3d-uefn"
        nothing = json.loads(find_workflows("zzz qqq"))
        assert nothing["workflows"] == [] and nothing["templates"] == [] and "No saved workflow fits" in nothing["hint"]
        # A template that fits is never a reason to make a workflow nobody asked for.
        only_templates = json.loads(find_workflows("sprite sheet pictures"))
        assert only_templates["workflows"] == [] and only_templates["templates"]
        assert "create_workflow_from_template" not in only_templates["hint"] and "No saved workflow fits" in only_templates["hint"]
        made = json.loads(create_workflow_from_template("builtin:pipe-sprite-sheet", name="My sheet"))
        assert made["ok"], made
        wid = made["workflow"]["id"]
        try:
            assert made["workflow"]["name"] == "My sheet" and len(made["workflow"]["graph"]["nodes"]) == 3
            assert any(e.get("type") == "graph_focus" and e.get("id") == wid for e in events)  # the editor opens on it
        finally:
            _delete(wid)
        assert json.loads(create_workflow_from_template("builtin:nope"))["ok"] is False
    finally:
        _delete(mine)


def test_save_workflow_refuses_an_unknown_team(monkeypatch):
    from backend.tools.panel.panel_automations import list_workflows, save_workflow

    _events(monkeypatch)
    out = json.loads(save_workflow(name="Shared", graph={"nodes": [], "edges": []}, owner="team_nobody"))
    assert out["ok"] is False and "unknown workflow owner" in out["error"]
    listed = json.loads(list_workflows())
    assert [o["kind"] for o in listed["owners"]] == ["local"]  # signed out: Local only
    assert all(w["name"] != "Shared" for w in listed["workflows"])


def test_custom_template_roundtrip(monkeypatch):
    from backend.tools.panel.panel_automations import delete_workflow_template, save_workflow_template

    events = _events(monkeypatch)
    raw = save_workflow_template(
        name="Starter",
        graph={"nodes": [{"id": "s", "type": "start.chat", "x": 0, "y": 0, "config": {}}], "edges": []},
    )
    doc = json.loads(raw)
    tid = doc["template"]["id"]
    try:
        assert doc["ok"] is True
        assert any(e.get("type") == "templates_changed" for e in events)
        assert json.loads(delete_workflow_template(tid))["ok"] is True
    finally:
        delete_workflow_template(tid)


def _graph():
    return {
        "nodes": [
            {"id": "s", "type": "start.manual", "x": 0, "y": 0, "config": {}},
            {"id": "a", "type": "flow.wait", "x": 280, "y": 0, "config": {"seconds": 1}, "label": "Pause", "locked": True},
            {"id": "b", "type": "flow.wait", "x": 560, "y": 0, "config": {}},
            {"id": "c", "type": "flow.wait", "x": 840, "y": 0, "config": {}},
        ],
        "edges": [{"source": "s", "target": "a", "kind": "main"}],
        "groups": [{"id": "g", "name": "Tail", "node_ids": ["c"], "locked": True}],
    }


def test_save_workflow_keeps_what_an_update_leaves_out(monkeypatch):
    from backend.automations.store import delete_workflow
    from backend.tools.panel.panel_automations import save_workflow

    _events(monkeypatch)
    wid = json.loads(save_workflow(name="Keep", description="Checks the lobby", enabled=False, graph=_graph()))["workflow"]["id"]
    try:
        out = json.loads(save_workflow(workflow_id=wid, name="Renamed"))
        assert out["ok"] is True
        wf = out["workflow"]
        assert wf["name"] == "Renamed" and wf["description"] == "Checks the lobby" and wf["enabled"] is False
        assert [n["id"] for n in wf["graph"]["nodes"]] == ["s", "a", "b", "c"]  # the graph was not wiped
    finally:
        delete_workflow(wid)


def test_save_workflow_holds_agents_to_the_users_locks(monkeypatch):
    from backend.automations.store import delete_workflow
    from backend.tools.panel.panel_automations import save_workflow

    events = _events(monkeypatch)
    wid = json.loads(save_workflow(name="Locked", graph=_graph()))["workflow"]["id"]
    try:
        moved = _graph()
        moved["nodes"][1]["x"] = 999
        moved["nodes"][3]["config"] = {"seconds": 5}
        out = json.loads(save_workflow(workflow_id=wid, graph=moved))
        assert out["ok"] is False and out["locked"] == ["Pause", "flow.wait"]
        unlocked_ok = _graph()
        unlocked_ok["nodes"][2]["x"] = 600
        events.clear()
        assert json.loads(save_workflow(workflow_id=wid, graph=unlocked_ok))["ok"] is True
        assert events[-1] == {"type": "graph_focus", "id": wid, "action": "saved", "nodes": ["b"]}  # only what changed
        assert json.loads(save_workflow(workflow_id=wid, graph=moved, allow_locked_changes=True))["ok"] is True
    finally:
        delete_workflow(wid)


def test_show_workflow_points_the_editor_at_nodes_and_groups(monkeypatch):
    from backend.automations.store import delete_workflow
    from backend.tools.panel.panel_automations import save_workflow, show_workflow

    events = _events(monkeypatch)
    wid = json.loads(save_workflow(name="Tour", graph=_graph()))["workflow"]["id"]
    try:
        events.clear()
        out = json.loads(show_workflow(wid, node_ids=["a", "zz"], note="This waits a second"))
        assert out == {"ok": True, "shown": ["a"], "missing": ["zz"]}
        assert events == [{"type": "graph_focus", "id": wid, "action": "show", "nodes": ["a"], "select": True, "note": "This waits a second"}]
        assert json.loads(show_workflow(wid, group_id="g", select=False))["shown"] == ["c"]
        assert json.loads(show_workflow(wid, group_id="nope"))["ok"] is False
        assert json.loads(show_workflow("missing-id"))["ok"] is False
    finally:
        delete_workflow(wid)


def test_save_workflow_refuses_data_wires_that_do_not_fit(monkeypatch):
    from backend.automations.store import delete_workflow
    from backend.tools.panel.panel_automations import save_workflow

    _events(monkeypatch)
    graph = {
        "nodes": [
            {"id": "n", "type": "input.number", "x": 0, "y": 0, "config": {"value": 3}},
            {"id": "img", "type": "input.image", "x": 300, "y": 0, "config": {}},
            {"id": "p", "type": "util.preview", "x": 600, "y": 0, "config": {}},
            {"id": "c", "type": "logic.compare", "x": 600, "y": 200, "config": {}},
        ],
        "edges": [
            {"source": "img", "target": "c", "kind": "data", "source_pin": "image", "target_pin": "nope"},
            {"source": "n", "target": "p", "kind": "data", "source_pin": "number", "target_pin": "value"},
        ],
    }
    out = json.loads(save_workflow(name="Wires", graph=graph))
    assert out["ok"] is False and out["wires"] == ["Compare has no input 'nope'"]
    graph["edges"][0]["target_pin"] = "a"
    out = json.loads(save_workflow(name="Wires", graph=graph))
    try:
        assert out["ok"] is True
    finally:
        delete_workflow(out["workflow"]["id"])


def test_a_team_members_new_workflow_is_filed_under_its_team_never_loose(monkeypatch):
    """A team's agent filled the person's list with 101-node "acceptance" workflows."""
    from backend.automations.store import delete_workflow, get_workflow
    from backend.tools.panel import panel_automations
    from backend.tools.panel.panel_automations import save_workflow

    _events(monkeypatch)
    graph = {"nodes": [{"id": "s", "type": "start.chat", "x": 0, "y": 0, "config": {}}], "edges": []}
    made = []
    try:
        monkeypatch.setattr(panel_automations, "_agent_maker", lambda: ("m1", "Timeline Editing", "Island Media Kit Delivery"))
        team = json.loads(save_workflow(name="Team made", graph=graph))["workflow"]
        made.append(team["id"])
        assert team["folder"] == "Made by agents/Island Media Kit Delivery"
        assert get_workflow(team["id"])["created_by"] == {"conv_id": "m1", "name": "Timeline Editing"}
        # Updating it never moves it.
        again = json.loads(save_workflow(workflow_id=team["id"], name="Renamed"))["workflow"]
        assert again["folder"] == "Made by agents/Island Media Kit Delivery"
        # A chat the person talks to directly files where it always did.
        monkeypatch.setattr(panel_automations, "_agent_maker", lambda: ("c1", "My chat", ""))
        solo = json.loads(save_workflow(name="Asked for", graph=graph))["workflow"]
        made.append(solo["id"])
        assert solo["folder"] == ""
    finally:
        for wid in made:
            delete_workflow(wid)


def _plugin_folder(folder, plugin_id, *, origin=""):
    import subprocess

    folder.mkdir(parents=True)
    (folder / "plugin.json").write_text(json.dumps({"id": plugin_id}), encoding="utf-8")
    if origin is not None:
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(folder)]
        subprocess.run([*git, "init", "-q", "-b", "main"], check=True, capture_output=True)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], check=True, capture_output=True)
        if origin:
            subprocess.run([*git, "remote", "add", "origin", origin], check=True, capture_output=True)
    return folder


def test_plugin_repo_found_in_ai_plugins_draft_with_origin(tmp_path):
    from backend.tools.panel.panel_automations import resolve_plugin_repo

    drafts = tmp_path / "ai_plugins"
    repo = _plugin_folder(drafts / "island-media-kit", "island-media-kit", origin="https://example.com/imk.git")
    out = resolve_plugin_repo("island-media-kit", roots=[str(tmp_path / "GitHub")], drafts=drafts)
    assert out["ok"] and out["source"] == "ai_plugins" and out["repo"] == repo.as_posix()
    assert out["origin"] == "https://example.com/imk.git" and out["summary"].endswith("(git, origin main)")


def test_plugin_repo_found_in_a_project_by_manifest_id(tmp_path):
    from backend.tools.panel.panel_automations import resolve_plugin_repo

    github = tmp_path / "GitHub"
    # Folder name differs from the id: found through plugin.json, and a git origin beats a bare draft.
    repo = _plugin_folder(github / "uefn-plugins" / "imk-clone", "island-media-kit", origin="git@x:imk.git")
    _plugin_folder(tmp_path / "ai_plugins" / "island-media-kit", "island-media-kit", origin=None)
    out = resolve_plugin_repo("island-media-kit", roots=[str(github)], drafts=tmp_path / "ai_plugins")
    assert out["ok"] and out["source"] == "project" and out["repo"] == repo.as_posix()
    # A project that is the plugin itself.
    alone = _plugin_folder(tmp_path / "solo", "solo-plugin", origin="")
    solo = resolve_plugin_repo("solo-plugin", roots=[str(alone)], drafts=tmp_path / "none")
    assert solo["ok"] and solo["repo"] == alone.as_posix() and solo["summary"].endswith("(git, no origin)")


def test_plugin_repo_missing_or_not_git_is_a_clear_error(tmp_path):
    from backend.tools.panel.panel_automations import resolve_plugin_repo

    out = resolve_plugin_repo("island-media-kit", roots=[str(tmp_path)], drafts=tmp_path / "ai_plugins")
    assert out["ok"] is False and "No repo for plugin 'island-media-kit'" in out["error"]
    _plugin_folder(tmp_path / "ai_plugins" / "island-media-kit", "island-media-kit", origin=None)
    bare = resolve_plugin_repo("island-media-kit", roots=[], drafts=tmp_path / "ai_plugins")
    assert bare["ok"] is False and "isn't a git repo" in bare["error"]
    assert resolve_plugin_repo(" ", roots=[], drafts=None)["ok"] is False


def test_list_workflow_nodes_for_one_plugin_is_small(monkeypatch):
    from backend.automations import catalog
    from backend.tools.panel.panel_automations import list_workflow_nodes

    schema = [{"id": f"f{i}", "label": "x" * 200, "type": "string"} for i in range(30)]
    nodes = [{"id": f"googledrive.n{i}", "plugin_id": "googledrive", "label": f"Node {i}", "config_fields": schema}
             for i in range(8)] + [{"id": "googledrive.gmail_send", "plugin_id": "googledrive", "config_fields": schema},
                                   {"id": "other.thing", "plugin_id": "other", "config_fields": schema}]
    triggers = [{"id": "gmail.received", "plugin_id": "googledrive", "label": "Email received"}]
    monkeypatch.setattr(catalog, "_contributions", lambda: (
        {"automations_nodes": nodes, "automations_triggers": triggers}, {"googledrive", "other"}))
    monkeypatch.setattr("backend.uefn_plugins.store.plugin_icon_data_url", lambda pid: "data:image/png;base64," + "A" * 200_000)

    raw = list_workflow_nodes(plugin="googledrive")
    rows = json.loads(raw)["nodes"]
    assert len(raw) < 10_000 and len(rows) == 10
    by_id = {r["id"]: r for r in rows}
    assert by_id["googledrive.gmail_send"]["kind"] == "node" and "other.thing" not in by_id
    assert by_id["gmail.received"]["kind"] == "trigger" and by_id["gmail.received"]["label"] == "Email received"
    assert all("config_fields" not in r and "icon" not in r for r in rows)
    with_schema = json.loads(list_workflow_nodes(plugin="googledrive", fields="config_fields"))["nodes"]
    assert len({r["id"]: r for r in with_schema}["googledrive.gmail_send"]["config_fields"]) == 30
    full = json.loads(list_workflow_nodes(plugin="googledrive", fields="all"))["nodes"]
    assert len(full) == 10 and full[0]["icon"].startswith("data:") and full[0]["plugin_id"] == "googledrive"
    assert any(n["type"] == "tool.call" for n in json.loads(list_workflow_nodes())["nodes"])  # no filter: unchanged


_RELEASE_GRAPH = {"nodes": [
    {"id": "in", "type": "flow.input", "config": {"inputs": [{"name": "plugin", "default": "island-media-kit"}]}},
    {"id": "repo", "type": "tool.call", "config": {"name": "ducky_plugin_find_repo", "arguments": {"plugin": "{{plugin}}"}}},
], "edges": []}


def test_release_plugin_of_reads_the_repo_step():
    from backend.tools.panel import panel_automations as panel

    def fake(wid):
        return {"id": wid, "graph": _RELEASE_GRAPH} if wid == "rel" else {"id": wid, "graph": {"nodes": []}}

    import backend.automations.store as store
    original = store.get_workflow
    store.get_workflow = fake
    try:
        assert panel.release_plugin_of("rel") == "island-media-kit"  # the input's default
        assert panel.release_plugin_of("rel", {"plugin": "playtester"}) == "playtester"
        assert panel.release_plugin_of("other") == ""
    finally:
        store.get_workflow = original


def test_release_workflow_holds_the_plugin_and_refuses_a_second_run(monkeypatch, tmp_path):
    from backend.automations import live_runs
    from backend.tools.panel import panel_ai_plugins
    from backend.tools.panel import panel_automations as panel

    monkeypatch.setattr(live_runs, "_directory", lambda: tmp_path / "runs")
    monkeypatch.setattr(panel_ai_plugins, "_releases_dir", lambda: tmp_path / "releases")
    notes: list[tuple] = []
    monkeypatch.setattr(panel_ai_plugins, "_append_step_note", lambda *a: notes.append(a))
    monkeypatch.setattr("backend.automations.store.get_workflow", lambda wid: {"id": wid, "graph": _RELEASE_GRAPH})
    monkeypatch.setattr(panel, "_reveal_graph", lambda *a, **k: None)
    monkeypatch.setattr(panel, "_review_hint", lambda out, *a: out)
    monkeypatch.setattr(panel, "resolve_plugin_repo", lambda pid: {"ok": True, "repo": "C:/repos/imk"})
    finish = threading.Event()
    started: list[str] = []

    def fake_run(wid, **kw):
        live_runs.start(wid, "7f3a")
        started.append(wid)
        finish.wait(5)
        return {"ok": True, "id": wid, "run": "7f3a"}

    monkeypatch.setattr("backend.automations.runner.run_workflow", fake_run)
    monkeypatch.setattr("backend.agent.a2a_client.send_notice", lambda **kw: None)

    async def check():
        first = asyncio.create_task(panel.run_workflow("rel", caller_conv_id="lead", plan_step_id="s1"))
        for _ in range(200):
            if (tmp_path / "releases" / "island-media-kit.json").exists():
                break
            await asyncio.sleep(0.02)
        rec = json.loads((tmp_path / "releases" / "island-media-kit.json").read_text(encoding="utf-8"))
        assert rec["run_id"] == "7f3a" and rec["step_id"] == "s1" and rec["chat_id"] == "lead"
        assert rec["clone"] == "C:/repos/imk" and notes == [("lead", "s1", "Release workflow run `7f3a` started.")]
        second = json.loads(await panel.run_workflow("rel"))
        assert second["ok"] is False and "release workflow 7f3a is running" in second["error"] and len(started) == 1
        assert json.loads(await first)["status"] == "running"
        finish.set()

    asyncio.run(check())


def _slow_run(monkeypatch):
    """A workflow whose step waits for ``finish``; the notices its end sends land in ``notices``."""
    from backend.tools.panel import panel_automations as panel

    finish, ended = threading.Event(), threading.Event()
    notices: list[dict] = []

    def slow(wid, **kw):
        finish.wait(10)
        ended.set()
        return {"ok": True, "id": wid, "run": kw["run_id"], "text": "Released 1.2.3"}

    monkeypatch.setattr("backend.automations.runner.run_workflow", slow)
    monkeypatch.setattr("backend.automations.store.get_workflow", lambda wid: {"id": wid, "name": "UEFN Ducky Release", "graph": {}})
    monkeypatch.setattr("backend.agent.a2a_client.send_notice", lambda **kw: notices.append(kw))
    monkeypatch.setattr(panel, "_reveal_graph", lambda *a, **k: None)
    return panel, finish, ended, notices


def test_run_workflow_returns_at_once_and_tells_the_caller_when_it_ends(monkeypatch):
    import time

    panel, finish, ended, notices = _slow_run(monkeypatch)

    async def check():
        began = time.monotonic()
        out = json.loads(await panel.run_workflow("rel", caller_conv_id="lead"))
        assert time.monotonic() - began < 1
        assert out["ok"] is True and out["status"] == "running" and out["workflow"] == "rel" and out["run_id"]
        assert notices == [] and not ended.is_set()
        finish.set()
        for _ in range(200):
            if notices:
                break
            await asyncio.sleep(0.01)
        return out["run_id"]

    run_id = asyncio.run(check())
    assert len(notices) == 1 and notices[0]["receiver_conv_id"] == "lead"
    assert f"UEFN Ducky Release finished: ok (run `{run_id}`)" in notices[0]["body"]
    assert "Released 1.2.3" in notices[0]["body"]


def test_cancelling_the_callers_turn_leaves_the_run_going(monkeypatch):
    panel, finish, ended, notices = _slow_run(monkeypatch)

    async def check():
        call = asyncio.create_task(panel.run_workflow("rel", caller_conv_id="lead", wait=True))
        await asyncio.sleep(0.05)
        call.cancel()
        try:
            await call
        except asyncio.CancelledError:
            pass
        finish.set()

    asyncio.run(check())
    assert ended.wait(5)  # the run finished after its caller's turn was cancelled
    assert notices == []  # wait=true: the caller's own tool result is the report
