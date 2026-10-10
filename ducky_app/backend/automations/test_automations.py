"""Save / emit / spawn stay on the current island; cron does not steal project."""

from __future__ import annotations

from datetime import datetime
from frontend.settings import PanelSettings
from frontend.ui_web.project_chats import _use_db, create_conversation, load_conversation, project_slug


def test_save_graph_roundtrip():
    from backend.automations.store import get_workflow, save_workflow

    wf = save_workflow(
        {
            "name": "Nightly",
            "enabled": True,
            "graph": {
                "nodes": [
                    {"id": "a", "type": "start.manual", "x": 10, "y": 20, "config": {}, "label": "Go"},
                    {"id": "b", "type": "ducky.spawn", "x": 200, "y": 20, "config": {"title": "Bot"}},
                ],
                "edges": [{"source": "a", "target": "b", "kind": "main"}],
            },
        }
    )
    got = get_workflow(wf["id"])
    assert got is not None
    assert got["name"] == "Nightly"
    assert len(got["graph"]["nodes"]) == 2
    assert got["graph"]["edges"][0]["source"] == "a"


def test_emit_runs_matching_workflows(monkeypatch):
    from backend.automations import runner
    from backend.automations.store import get_workflow, save_workflow

    monkeypatch.setattr(runner, "_run_message", lambda *a, **k: "run")
    save_workflow(
        {
            "name": "Mail",
            "enabled": True,
            "graph": {
                "nodes": [
                    {"id": "t", "type": "email.received", "x": 0, "y": 0, "config": {}},
                    {"id": "w", "type": "flow.wait", "x": 40, "y": 0, "config": {"seconds": 0}},
                ],
                "edges": [{"source": "t", "target": "w", "kind": "main"}],
            },
        }
    )
    out = runner.emit_trigger("email.received", {"subject": "hi"})
    assert out["ok"] is True
    assert out["runs"]
    assert out["runs"][0]["ok"] is True
    assert any(s.get("type") == "flow.wait" for s in out["runs"][0]["steps"])
    logged = get_workflow(out["runs"][0]["id"])
    assert logged and logged["runs"]


def test_spawn_stays_on_current_island(tmp_path, monkeypatch):
    from backend.automations import runner
    from backend.automations.store import save_workflow

    home = tmp_path / "HomeIsland"
    other = tmp_path / "OtherIsland"
    home.mkdir()
    other.mkdir()
    s = PanelSettings.load()
    s.uefn_project_root = str(home)
    s.save()
    monkeypatch.setattr(runner, "_run_message", lambda *a, **k: "run")
    wf = save_workflow(
        {
            "name": "Spawn",
            "enabled": True,
            "graph": {
                "nodes": [
                    {"id": "m", "type": "start.manual", "x": 0, "y": 0, "config": {}},
                    {
                        "id": "s",
                        "type": "ducky.spawn",
                        "x": 80,
                        "y": 0,
                        "config": {"title": "Auto ducky", "prompt": "hello"},
                    },
                ],
                "edges": [{"source": "m", "target": "s", "kind": "main"}],
            },
        }
    )
    before = s.uefn_project_root
    result = runner.run_workflow(wf["id"])
    assert result["ok"] is True
    conv_id = result.get("conv_id") or ""
    assert conv_id
    after = PanelSettings.load()
    assert after.uefn_project_root == before
    loaded = load_conversation(conv_id)
    assert loaded is not None
    assert loaded.title == "Auto ducky"
    if _use_db():
        from frontend.ui_web.project_chats import _repo

        assert _repo().conv_project_id(conv_id) == project_slug(str(home))


def test_cron_does_not_steal_project(tmp_path, monkeypatch):
    from backend.automations import runner
    from backend.automations.scheduler import cron_due, interval_due
    from backend.automations.store import save_workflow

    home = tmp_path / "IslandA"
    home.mkdir()
    s = PanelSettings.load()
    s.uefn_project_root = str(home)
    s.save()
    monkeypatch.setattr(runner, "_run_message", lambda *a, **k: "run")
    wf = save_workflow(
        {
            "name": "Tick",
            "enabled": True,
            "graph": {
                "nodes": [
                    {"id": "c", "type": "start.cron", "x": 0, "y": 0, "config": {"interval_seconds": 1}},
                    {"id": "s", "type": "ducky.spawn", "x": 80, "y": 0, "config": {"title": "Cron ducky"}},
                ],
                "edges": [{"source": "c", "target": "s", "kind": "main"}],
            },
        }
    )
    root_before = PanelSettings.load().uefn_project_root
    result = runner.run_workflow(wf["id"], starter_id="c")
    assert result["ok"] is True
    assert PanelSettings.load().uefn_project_root == root_before
    assert interval_due(60, 0, 10) is True
    assert interval_due(60, 100, 120) is False
    now = datetime(2026, 9, 14, 6, 30, 0)
    assert cron_due("30 6 * * *", now, 0) is True
    assert cron_due("0 6 * * *", now, 0) is False
    create_conversation(PanelSettings.load(), "", title="keep", project_root=str(home))
    assert PanelSettings.load().uefn_project_root == str(home)


def test_catalog_has_builtin_ducky_nodes():
    from backend.automations.catalog import list_nodes

    types = {n["type"] for n in list_nodes()}
    assert {
        "start.manual",
        "start.cron",
        "ducky.prompt",
        "ducky.spawn",
        "flow.wait",
        "flow.foreach",
        "flow.branch",
        "tool.call",
    } <= types


def test_emit_skips_when_trigger_config_channel_differs(monkeypatch):
    from backend.automations import runner
    from backend.automations.store import save_workflow

    monkeypatch.setattr(runner, "_run_message", lambda *a, **k: "run")
    save_workflow(
        {
            "name": "Chan",
            "enabled": True,
            "graph": {
                "nodes": [
                    {
                        "id": "t",
                        "type": "discord.message",
                        "x": 0,
                        "y": 0,
                        "config": {"channel_id": "want"},
                    },
                    {"id": "w", "type": "flow.wait", "x": 40, "y": 0, "config": {"seconds": 0}},
                ],
                "edges": [{"source": "t", "target": "w", "kind": "main"}],
            },
        }
    )
    miss = runner.emit_trigger("discord.message", {"channel_id": "other", "content": "nope"})
    assert miss["ok"] is True
    assert miss["runs"] == []
    hit = runner.emit_trigger("discord.message", {"channel_id": "want", "content": "yes"})
    assert hit["runs"]
    assert hit["runs"][0]["ok"] is True
    assert any(s.get("type") == "flow.wait" for s in hit["runs"][0]["steps"])


def test_a_plugin_step_with_no_plugin_to_run_it_fails_and_says_so(monkeypatch):
    """It used to pass as if it had run: the workflow said done having skipped it."""
    from backend.automations import catalog, runner
    from backend.automations.store import save_workflow

    def wf(step: str) -> str:
        return save_workflow({"name": f"Uses {step}", "enabled": True, "graph": {
            "nodes": [{"id": "s", "type": "start.manual", "x": 0, "y": 0, "config": {}},
                      {"id": "p", "type": step, "x": 300, "y": 0, "config": {}, "label": "Plugin step"},
                      {"id": "w", "type": "flow.wait", "x": 600, "y": 0, "config": {"seconds": 0}}],
            "edges": [{"source": "s", "target": "p", "kind": "main"}, {"source": "p", "target": "w", "kind": "main"}]}})["id"]

    gone = runner.run_workflow(wf("browser.open"))
    assert gone["ok"] is False and "Install or turn on the plugin" in gone["error"]
    assert not any(s.get("type") == "flow.wait" for s in gone["steps"])
    # On, but its handler isn't registered (yet): a different message, still not skipped.
    specs = catalog.node_specs()
    monkeypatch.setattr(catalog, "node_specs", lambda: {**specs, "browser.open": {
        "type": "browser.open", "role": "action", "plugin_id": "browser"}})
    loading = runner.run_workflow(wf("browser.open"))
    assert loading["ok"] is False and "browser plugin hasn't loaded" in loading["error"]


def test_one_list_holds_every_workflow_with_what_starts_it():
    from backend.automations.store import get_workflow, list_workflows, save_workflow

    timer = save_workflow({"name": "Timer", "graph": {"nodes": [
        {"id": "c", "type": "start.cron", "x": 0, "y": 0, "config": {"interval_seconds": 300}}], "edges": []}})
    chat = save_workflow({"name": "Chat", "description": "draw then cut", "graph": {"nodes": [
        {"id": "s", "type": "start.chat", "x": 0, "y": 0, "config": {}}], "edges": []}})
    got = get_workflow(chat["id"])
    assert got is not None and "kind" not in got
    assert got["description"] == "draw then cut"
    rows = {r["id"]: r for r in list_workflows()}
    assert rows[timer["id"]]["trigger"] == {"kind": "schedule", "label": "Every 5m"}
    assert rows[chat["id"]]["trigger"] == {"kind": "chat", "label": "Chat"}
    assert rows[chat["id"]]["owner"]["kind"] == "local"


def test_one_catalog_for_every_workflow():
    from backend.automations.catalog import list_nodes

    nodes = list_nodes()
    types = {n["type"] for n in nodes}
    assert {"start.chat", "start.cron", "start.manual", "pipeline.agent", "pipeline.finish", "ducky.spawn", "tool.call"} <= types
    assert all("systems" not in n for n in nodes)


def test_catalog_keeps_select_options(monkeypatch):
    from backend.automations import catalog
    from backend.uefn_plugins import host, store as pstore

    monkeypatch.setattr(
        host,
        "get_ui_contributions",
        lambda: {
            "automations_nodes": [
                {
                    "id": "openai.complete",
                    "label": "OpenAI complete",
                    "plugin_id": "openai",
                    "config_fields": [
                        {"id": "model", "type": "model", "provider": "openai"},
                        {
                            "id": "size",
                            "type": "select",
                            "options": [{"id": "1024x1024", "label": "1024×1024"}],
                        },
                    ],
                }
            ],
            "automations_triggers": [],
        },
    )
    monkeypatch.setattr(pstore, "get_enabled_plugin_ids", lambda: ["openai"])
    node = next(n for n in catalog.list_nodes() if n["type"] == "openai.complete")
    model = next(f for f in node["config_fields"] if f["id"] == "model")
    size = next(f for f in node["config_fields"] if f["id"] == "size")
    assert model["type"] == "model" and model["provider"] == "openai"
    assert size["options"][0]["id"] == "1024x1024"


def test_plugin_node_systems_are_ignored(monkeypatch):
    from backend.automations import catalog
    from backend.uefn_plugins import host, store as pstore

    monkeypatch.setattr(
        host,
        "get_ui_contributions",
        lambda: {
            "automations_nodes": [
                {
                    "id": "image.rembg",
                    "label": "Rembg",
                    "plugin_id": "cut",
                    "systems": ["pipeline"],
                }
            ],
            "automations_triggers": [],
        },
    )
    monkeypatch.setattr(pstore, "get_enabled_plugin_ids", lambda: ["cut"])
    # A tile once limited to Pipelines is on the one Workflows palette.
    assert "image.rembg" in {n["type"] for n in catalog.list_nodes()}


def test_chat_run_stamps_caller_and_finish_posts(monkeypatch):
    from backend.automations import runner
    from backend.automations.store import save_workflow
    from backend.workspace import identity
    from backend.workspace.identity import RunContext

    caller = create_conversation(PanelSettings.load(), "", title="Caller")
    wf = save_workflow(
        {
            "name": "Return",
            "graph": {
                "nodes": [
                    {"id": "s", "type": "start.chat", "x": 0, "y": 0, "config": {}},
                    {"id": "f", "type": "pipeline.finish", "x": 80, "y": 0, "config": {"message": "pipeline done"}},
                ],
                "edges": [{"source": "s", "target": "f", "kind": "main"}],
            },
        }
    )
    monkeypatch.setattr(
        runner,
        "_ensure_pipeline_group",
        lambda ctx, wf: ctx.update({"group_id": "hub-ret", "group_folder_id": "hub-folder", "conv_id": "hub-ret"}),
    )
    token = identity.bind(RunContext(run_id="r1", conv_id=caller.id))
    try:
        out = runner.run_workflow(wf["id"], prompt="hi")
    finally:
        identity.reset(token)
    assert out["ok"] is True
    loaded = load_conversation(caller.id)
    assert loaded is not None
    texts = [str(m.get("content") or m.get("text") or "") for m in loaded.messages]
    assert any("pipeline done" in t for t in texts)


def test_pipeline_agent_waits_and_keeps_files(monkeypatch):
    from backend.automations import runner
    from backend.automations.artifacts import chat_dir
    from backend.automations.store import save_workflow

    monkeypatch.setattr(
        runner,
        "_resolve_profile",
        lambda d: {"id": "artist", "name": "Artist", "ducky_style": "artist"},
    )
    monkeypatch.setattr(
        runner,
        "_agent_spawn_kwargs",
        lambda p: {"ducky_style": "artist", "ducky_name": "Artist", "profile_id": "artist"},
    )

    def fake_wait(conv_id, text, mode, model, *, timeout_sec, parent="", attachments=None, started_by=None):
        dest = chat_dir(conv_id) / "out.png"
        dest.write_bytes(b"png")
        fake_wait.attachments = attachments
        return {"status": "done", "assistant_text": "made it", "conv_id": conv_id}

    fake_wait.attachments = None
    monkeypatch.setattr(runner, "_run_message_and_wait", fake_wait)
    monkeypatch.setattr(
        runner,
        "_seat_agent_cluster",
        lambda cfg, payload, profile, kwargs: {
            "ok": True,
            "conv_id": "worker-1",
            "group_id": "nest-1",
            "group_folder_id": "nest-folder",
        },
    )
    monkeypatch.setattr(runner, "_ensure_pipeline_group", lambda ctx, wf: ctx.update({"group_id": "hub-1", "group_folder_id": "hub-folder", "conv_id": "hub-1"}))
    caller = create_conversation(PanelSettings.load(), "", title="Art caller")
    wf = save_workflow(
        {
            "name": "Draw",
            "graph": {
                "nodes": [
                    {"id": "s", "type": "start.chat", "x": 0, "y": 0, "config": {}},
                    {
                        "id": "a",
                        "type": "pipeline.agent",
                        "x": 80,
                        "y": 0,
                        "config": {"ducky": "artist", "prompt": "draw"},
                    },
                ],
                "edges": [{"source": "s", "target": "a", "kind": "main"}],
            },
        }
    )
    out = runner.run_workflow(wf["id"], prompt="a duck", caller_conv_id=caller.id)
    assert out["ok"] is True
    files = out.get("files") or []
    assert files
    assert any("out.png" in str(f.get("path") if isinstance(f, dict) else f) for f in files)
    worker = out.get("conv_id") or ""
    assert worker and worker != caller.id


def test_scheduler_runs_every_local_workflow_with_a_schedule(monkeypatch):
    from backend.automations import scheduler
    from backend.automations.store import save_workflow

    hits: list[str] = []
    monkeypatch.setattr(
        scheduler,
        "run_workflow",
        lambda wid, **k: hits.append(wid) or {"ok": True},
    )
    wf = save_workflow(
        {
            "name": "Tick",
            "enabled": True,
            "graph": {
                "nodes": [
                    {"id": "s", "type": "start.chat", "x": 0, "y": 0, "config": {}},
                    {"id": "c", "type": "start.cron", "x": 0, "y": 0, "config": {"interval_seconds": 1}},
                ],
                "edges": [],
            },
        }
    )
    for thread in scheduler._tick():
        thread.join(5)
    assert hits == [wf["id"]]


def test_custom_automation_template_roundtrip():
    from backend.automations.templates import delete_custom, list_custom, save_custom

    row = save_custom(
        "Ping",
        description="demo",
        graph={
            "nodes": [{"id": "m", "type": "start.manual", "x": 0, "y": 0, "config": {}}],
            "edges": [],
        },
    )
    assert row["id"].startswith("custom:")
    listed = list_custom()
    got = next(t for t in listed if t["id"] == row["id"])
    assert got["graph"]["nodes"][0]["type"] == "start.manual"
    assert delete_custom(row["id"]) is True
    assert not any(t["id"] == row["id"] for t in list_custom())


class _FakeGroups:
    def __init__(self):
        self.created: list[dict] = []
        self.added: list[tuple] = []
        self.invited: list[tuple] = []
        self._n = 0

    def group_create(self, name: str = "", folder_id: str = "", open_tab: bool = True):
        self._n += 1
        hid = f"hub-{self._n}"
        self.created.append({"name": name, "folder_id": folder_id, "id": hid, "open_tab": open_tab})
        return {"ok": True, "id": hid, "folder_id": folder_id or f"folder-{self._n}"}

    def group_find_or_create(self, name: str = "", folder_id: str = "", open_tab: bool = False):
        return self.group_create(name=name, folder_id=folder_id, open_tab=open_tab)

    def group_add_member(self, group_id: str, conv_id: str, as_leader: bool = False):
        self.added.append((group_id, conv_id, as_leader))
        return {"ok": True, "leader_conv_id": conv_id if as_leader else ""}

    def group_invite(self, group_id: str, profile_id: str, model: str = "", write_allowed=None):
        self.invited.append((group_id, profile_id))
        return {"ok": True, "member": {"member_conv_id": f"mem-{profile_id}"}}


def test_wait_pipeline_skips_group(monkeypatch):
    from backend.automations import runner
    from backend.automations.store import save_workflow

    fake = _FakeGroups()
    monkeypatch.setattr("backend.tools.panel.ducky_panel._panel_api", lambda: fake)
    caller = create_conversation(PanelSettings.load(), "", title="Solo")
    wf = save_workflow(
        {
            "name": "No swarm",
            "graph": {
                "nodes": [
                    {"id": "s", "type": "start.chat", "x": 0, "y": 0, "config": {}},
                    {"id": "w", "type": "flow.wait", "x": 40, "y": 0, "config": {"seconds": 0}},
                ],
                "edges": [{"source": "s", "target": "w", "kind": "main"}],
            },
        }
    )
    out = runner.run_workflow(wf["id"], prompt="go", caller_conv_id=caller.id)
    assert out["ok"] is True
    assert fake.created == []
    assert fake.added == []


def test_pipeline_group_seats_solo_caller(monkeypatch):
    from backend.automations import runner
    from backend.automations.store import save_workflow
    from frontend.ui_web.project_chats import load_conversation, save_conversation

    fake = _FakeGroups()
    monkeypatch.setattr("backend.tools.panel.ducky_panel._panel_api", lambda: fake)
    caller = create_conversation(PanelSettings.load(), "", title="Solo")
    caller.parent_conv_id = ""
    save_conversation(caller)
    wf = save_workflow(
        {
            "name": "Clustered",
            "graph": {
                "nodes": [
                    {"id": "s", "type": "start.chat", "x": 0, "y": 0, "config": {}},
                    {"id": "w", "type": "flow.wait", "x": 40, "y": 0, "config": {"seconds": 0}},
                    {"id": "a", "type": "pipeline.agent", "x": 80, "y": 0, "config": {}},
                ],
                "edges": [{"source": "s", "target": "w", "kind": "main"}],
            },
        }
    )
    out = runner.run_workflow(wf["id"], prompt="go", caller_conv_id=caller.id)
    assert out["ok"] is True
    assert fake.created and fake.created[0].get("open_tab") is False
    assert fake.added == []
    hub = load_conversation(fake.created[0]["id"])
    if hub is not None:
        assert getattr(hub, "leader_conv_id", "") == caller.id


def test_pipeline_group_does_not_move_grouped_caller(monkeypatch):
    from backend.automations import runner
    from backend.automations.store import save_workflow
    from frontend.ui_web.project_chats import load_conversation, save_conversation

    fake = _FakeGroups()
    monkeypatch.setattr("backend.tools.panel.ducky_panel._panel_api", lambda: fake)
    hub = create_conversation(PanelSettings.load(), "", title="Existing swarm")
    hub.is_group = True
    save_conversation(hub)
    caller = create_conversation(PanelSettings.load(), "", title="Member", parent_conv_id=hub.id)
    save_conversation(caller)
    wf = save_workflow(
        {
            "name": "Nested",
            "graph": {
                "nodes": [
                    {"id": "s", "type": "start.chat", "x": 0, "y": 0, "config": {}},
                    {"id": "w", "type": "flow.wait", "x": 40, "y": 0, "config": {"seconds": 0}},
                    {"id": "a", "type": "pipeline.agent", "x": 80, "y": 0, "config": {}},
                ],
                "edges": [{"source": "s", "target": "w", "kind": "main"}],
            },
        }
    )
    out = runner.run_workflow(wf["id"], caller_conv_id=caller.id)
    assert out["ok"] is True
    assert fake.created and fake.created[0].get("open_tab") is False
    assert fake.added == []
    hub_run = load_conversation(fake.created[0]["id"])
    if hub_run is not None:
        assert getattr(hub_run, "leader_conv_id", "") == caller.id


def test_pipeline_agent_forwards_image_attachments(monkeypatch, tmp_path):
    from backend.automations import runner
    from backend.automations.store import save_workflow

    seen = {}

    def fake_wait(conv_id, text, mode, model, *, timeout_sec, parent="", attachments=None, started_by=None):
        seen["attachments"] = attachments
        return {"status": "done", "assistant_text": "saw it", "conv_id": conv_id}

    monkeypatch.setattr(runner, "_resolve_profile", lambda d: {"id": "artist", "name": "Artist"})
    monkeypatch.setattr(runner, "_agent_spawn_kwargs", lambda p: {"ducky_name": "Artist", "profile_id": "artist"})
    monkeypatch.setattr(
        runner,
        "_seat_agent_cluster",
        lambda cfg, payload, profile, kwargs: {"ok": True, "conv_id": "w1", "group_id": "n1", "group_folder_id": "nf"},
    )
    monkeypatch.setattr(runner, "_run_message_and_wait", fake_wait)
    monkeypatch.setattr(runner, "_ensure_pipeline_group", lambda ctx, wf: None)
    img = tmp_path / "duck.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    caller = create_conversation(PanelSettings.load(), "", title="Img")
    wf = save_workflow(
        {
            "name": "See",
            "graph": {
                "nodes": [
                    {"id": "s", "type": "start.chat", "x": 0, "y": 0, "config": {}},
                    {"id": "a", "type": "pipeline.agent", "x": 40, "y": 0, "config": {"ducky": "artist", "prompt": "look"}},
                ],
                "edges": [{"source": "s", "target": "a", "kind": "main"}],
            },
        }
    )
    out = runner.run_workflow(wf["id"], caller_conv_id=caller.id, files=[{"path": str(img), "name": "duck.png"}])
    assert out["ok"] is True
    atts = seen.get("attachments") or []
    assert atts and atts[0]["kind"] == "image"
    assert atts[0]["name"] == "duck.png"


def test_flow_branch_contains_and_exists():
    from backend.automations.runner import _eval_branch

    payload = {"text": "shop granted gold", "files": [{"name": "out.png"}], "result": {"ok": True}}
    assert _eval_branch({"field": "text", "contains": "granted"}, payload) is True
    assert _eval_branch({"field": "text", "contains": "nope"}, payload) is False
    assert _eval_branch({"field": "files", "op": "exists"}, payload) is True
    assert _eval_branch({"field": "missing", "op": "exists"}, payload) is False
    assert _eval_branch({"field": "result.ok", "equals": "True"}, payload) is True


def test_flow_branch_agent_mode(monkeypatch):
    from backend.automations import runner
    from backend.automations.store import save_workflow

    monkeypatch.setattr(runner, "_ensure_pipeline_group", lambda ctx, wf: None)
    monkeypatch.setattr(
        runner,
        "_pipeline_agent",
        lambda cfg, payload: {"ok": True, "result": {"text": "yes, approve this"}},
    )
    wf = save_workflow(
        {
            "name": "Judge",
            "graph": {
                "nodes": [
                    {"id": "s", "type": "start.chat", "x": 0, "y": 0, "config": {}},
                    {
                        "id": "b",
                        "type": "flow.branch",
                        "x": 40,
                        "y": 0,
                        "config": {"mode": "agent", "ducky": "judge", "prompt": "ok?"},
                    },
                    {"id": "w", "type": "flow.wait", "x": 80, "y": 0, "config": {"seconds": 0}},
                ],
                "edges": [
                    {"source": "s", "target": "b", "kind": "main"},
                    {"source": "b", "target": "w", "kind": "true"},
                ],
            },
        }
    )
    out = runner.run_workflow(wf["id"])
    assert out["ok"] is True
    assert any(s.get("type") == "flow.wait" for s in out["steps"])

    monkeypatch.setattr(
        runner,
        "_pipeline_agent",
        lambda cfg, payload: {"ok": True, "result": {"text": "no reject"}},
    )
    out2 = runner.run_workflow(wf["id"])
    assert out2["ok"] is True
    assert not any(s.get("type") == "flow.wait" for s in out2["steps"])


def test_list_templates_stamps_missing_plugins(monkeypatch):
    from backend.automations import templates
    from backend.uefn_plugins import host, store as pstore

    monkeypatch.setattr(
        host,
        "get_ui_contributions",
        lambda: {
            "automations_templates": [
                {
                    "id": "image-to-island",
                    "label": "Image to island",
                    "plugin_id": "account",
                    "systems": ["pipeline"],
                    "requires_plugins": ["meshy", "blender"],
                    "graph": {
                        "nodes": [
                            {"id": "s", "type": "start.chat", "x": 0, "y": 0, "config": {}},
                            {"id": "m", "type": "tool.call", "x": 1, "y": 0, "config": {"name": "meshy_image_to_3d"}},
                        ],
                        "edges": [],
                    },
                }
            ]
        },
    )
    monkeypatch.setattr(pstore, "get_enabled_plugin_ids", lambda: ["account"])
    rows = templates.list_templates()
    row = next(t for t in rows if t["id"].endswith("image-to-island"))
    assert row["ready"] is False
    assert "meshy" in row["missing_plugins"]
    assert "blender" in row["missing_plugins"]


def test_foreach_runs_body_per_item():
    from backend.automations import runner
    from backend.automations.store import save_workflow

    wf = save_workflow(
        {
            "name": "Each",
            "enabled": True,
            "graph": {
                "nodes": [
                    {"id": "s", "type": "start.manual", "x": 0, "y": 0, "config": {}},
                    {"id": "f", "type": "flow.foreach", "x": 40, "y": 0, "config": {"field": "cards"}},
                    {"id": "w", "type": "flow.wait", "x": 80, "y": 0, "config": {"seconds": 0}},
                    {"id": "d", "type": "flow.wait", "x": 120, "y": 0, "config": {"seconds": 0}, "label": "done"},
                ],
                "edges": [
                    {"source": "s", "target": "f", "kind": "main"},
                    {"source": "f", "target": "w", "kind": "each"},
                    {"source": "f", "target": "d", "kind": "done"},
                ],
            },
        }
    )
    out = runner.run_workflow(wf["id"], payload={"cards": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]})
    assert out["ok"] is True
    waits = [s for s in out["steps"] if s.get("type") == "flow.wait"]
    assert sum(1 for s in waits if s.get("id") == "w") == 2
    assert sum(1 for s in waits if s.get("id") == "d") == 1
    empty = runner.run_workflow(wf["id"], payload={"cards": []})
    assert empty["ok"] is True
    empty_waits = [s for s in empty["steps"] if s.get("type") == "flow.wait"]
    assert sum(1 for s in empty_waits if s.get("id") == "w") == 0
    assert sum(1 for s in empty_waits if s.get("id") == "d") == 1


def test_foreach_keeps_going_past_a_failed_item_when_asked(monkeypatch):
    from backend.automations import plugin, runner
    from backend.automations.store import save_workflow

    done: list[str] = []

    def handler(ctx):
        card = ctx["payload"].get("card_id")
        if card == "b":
            return {"ok": False, "error": "card b refused"}
        done.append(card)
        return {"ok": True}

    monkeypatch.setattr("backend.uefn_plugins.host.is_plugin_enabled", lambda pid: pid == "testplug")
    plugin.register_node("testplug", "test.one_card", handler)

    def graph(keep_going):
        return save_workflow({
            "name": "Each card",
            "graph": {
                "nodes": [
                    {"id": "s", "type": "start.manual", "x": 0, "y": 0, "config": {}},
                    {"id": "f", "type": "flow.foreach", "x": 40, "y": 0, "config": {"field": "cards", "continue_on_error": keep_going}},
                    {"id": "one", "type": "test.one_card", "x": 80, "y": 0, "config": {}},
                    {"id": "d", "type": "flow.wait", "x": 120, "y": 0, "config": {"seconds": 0}},
                ],
                "edges": [
                    {"source": "s", "target": "f", "kind": "main"},
                    {"source": "f", "target": "one", "kind": "each"},
                    {"source": "f", "target": "d", "kind": "done"},
                ],
            },
        })["id"]

    cards = {"cards": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}
    try:
        stops = runner.run_workflow(graph(False), payload=cards)  # default: the first failure ends the run
        assert stops["ok"] is False and "card b refused" in stops["error"] and done == ["a"]
        assert not any(s.get("id") == "d" for s in stops["steps"])
        done.clear()
        out = runner.run_workflow(graph(True), payload=cards)
    finally:
        plugin.clear_for_plugin("testplug")
    assert out["ok"] is True, out
    assert done == ["a", "c"]
    each = next(s for s in out["steps"] if s.get("id") == "f")
    assert each["result"]["count"] == 3
    assert each["result"]["failed"] == [{"index": 1, "error": "card b refused"}]
    assert out["node_outputs"]["f"] == {"count": 3, "failed": [{"index": 1, "error": "card b refused"}]}
    assert sum(1 for s in out["steps"] if s.get("id") == "d") == 1


def test_pipeline_finish_attaches_png(tmp_path, monkeypatch):
    from backend.automations import runner
    from backend.automations.store import save_workflow
    from backend.workspace import identity
    from backend.workspace.identity import RunContext

    png = tmp_path / "card.png"
    png.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc``\x00\x00\x00\x04\x00\x01"
        b"\xdd\x8d\xb4\x1c\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    caller = create_conversation(PanelSettings.load(), "", title="CallerImg")
    wf = save_workflow(
        {
            "name": "Img",
            "graph": {
                "nodes": [
                    {"id": "s", "type": "start.chat", "x": 0, "y": 0, "config": {}},
                    {"id": "f", "type": "pipeline.finish", "x": 80, "y": 0, "config": {"message": "art ready"}},
                ],
                "edges": [{"source": "s", "target": "f", "kind": "main"}],
            },
        }
    )
    monkeypatch.setattr(
        runner,
        "_ensure_pipeline_group",
        lambda ctx, wf: ctx.update({"group_id": "hub-img", "conv_id": "hub-img"}),
    )
    token = identity.bind(RunContext(run_id="rimg", conv_id=caller.id))
    try:
        out = runner.run_workflow(wf["id"], prompt="hi", files=[{"path": str(png), "name": "card.png"}])
    finally:
        identity.reset(token)
    assert out["ok"] is True
    loaded = load_conversation(caller.id)
    assert loaded is not None
    asst = [m for m in loaded.messages if isinstance(m, dict) and m.get("role") == "assistant"]
    assert asst
    atts = asst[-1].get("attachments") or []
    assert atts and atts[0].get("kind") == "image"


def test_run_announces_header_jobs(monkeypatch):
    from backend.automations import runner
    from backend.automations.store import save_workflow

    events: list[dict] = []
    monkeypatch.setattr(runner, "_run_message", lambda *a, **k: "run")
    monkeypatch.setattr("frontend.ui_web.agent_modes.push_ui_event", events.append)
    wf = save_workflow(
        {
            "name": "Tray",
            "enabled": True,
            "graph": {
                "nodes": [{"id": "m", "type": "start.manual", "x": 0, "y": 0, "config": {}}],
                "edges": [],
            },
        }
    )
    out = runner.run_workflow(wf["id"])
    assert out["ok"] is True
    jobs = [e for e in events if e.get("type") == "background_job"]
    phases = [e.get("phase") for e in jobs]
    assert "working" in phases
    assert "done" in phases
    assert any(e.get("id") == f"graph-run:{wf['id']}:{out['run']}" and e.get("phase") == "working" for e in jobs)
    assert len({e["id"] for e in jobs}) == 1  # finish updates this exact run
    assert any(e.get("type") == "graphs_changed" for e in events)


def test_uefn_wait_ready_node_reports_timeout(monkeypatch):
    from backend.automations import runner

    monkeypatch.setattr(
        "frontend.window_view.wait_uefn_ready",
        lambda **_k: {"ok": False, "error": "timed out waiting for UEFN", "project_match": False},
    )
    step = runner._exec_node(
        {"id": "w", "type": "uefn.wait_ready", "config": {"timeout": 1}},
        {},
    )
    assert step["ok"] is False
    assert "timed out" in step["error"]


def test_builtin_uefn_templates_listed():
    from backend.automations.templates import list_templates

    ids = {row["id"] for row in list_templates()}
    assert {"builtin:restart-uefn", "builtin:publish-private", "builtin:memory-calculation"} <= ids
    from backend.automations.templates import _MEMORY_PROMPT, _PRIVATE_PROMPT

    for prompt in (_PRIVATE_PROMPT, _MEMORY_PROMPT):
        assert "Calculate Memory" not in prompt
        assert "Publish Project" not in prompt
    assert "Upload to Private Version" in _PRIVATE_PROMPT
    assert "press OK" in _PRIVATE_PROMPT
    assert "Launch on this PC" in _MEMORY_PROMPT
    assert "Launch Memory Calculation" in _MEMORY_PROMPT
    assert "press OK" in _MEMORY_PROMPT


def test_catalog_has_play_test_nodes():
    from backend.automations.catalog import list_nodes

    by_type = {n["type"]: n for n in list_nodes()}
    assert {"uefn.check", "uefn.game.start", "uefn.game.stop", "uefn.player.wait", "uefn.log.expect"} <= set(by_type)
    assert all(by_type[t]["group"] == "Play test" for t in ("uefn.check", "uefn.game.start", "uefn.game.stop"))
    assert by_type["uefn.game.start"]["config_fields"][0]["id"] == "skip_if_playing"


def test_builtin_templates_are_wired_to_known_nodes_and_ready():
    from backend.automations.catalog import list_nodes
    from backend.automations.templates import list_templates

    known = {n["type"] for n in list_nodes()}
    rows = {row["id"]: row for row in list_templates() if row["kind"] == "builtin"}
    play = {
        "builtin:playtest-start",
        "builtin:stop-game",
        "builtin:tycoon-first-purchase",
        "builtin:tycoon-income-loop",
        "builtin:tycoon-ducky-playtest",
    }
    assert play <= set(rows)
    for row in rows.values():
        # Ready-made media pipelines need their plugins (3D AI Studio, Meshy, UEFN…); the rest are always ready.
        assert set(row["missing_plugins"]) <= set(row["requires_plugins"]), row["id"]
        if not row["requires_plugins"]:
            assert row["ready"], row["id"]
        ids = {n["id"] for n in row["graph"]["nodes"]}
        for node in row["graph"]["nodes"]:
            assert node["type"] in known, (row["id"], node["type"])
        for edge in row["graph"]["edges"]:
            assert edge["source"] in ids and edge["target"] in ids, (row["id"], edge)
        if row["id"] not in play:
            continue
        # Every non-end play-test node leads somewhere — a dangling wire silently stops the run.
        sources = {e["source"] for e in row["graph"]["edges"]}
        for node in row["graph"]["nodes"]:
            if node["type"] not in ("pipeline.finish", "flow.end"):
                assert node["id"] in sources, (row["id"], node["id"])


def test_play_templates_check_before_launching():
    """Start game is reached from both the running and the closed path; Open UEFN
    project only on the closed one; Start game never launches twice."""
    from backend.automations.templates import list_templates

    for row in list_templates():
        if row["id"] not in ("builtin:playtest-start", "builtin:tycoon-income-loop"):
            continue
        graph = row["graph"]
        kinds = {(e["source"], e["kind"]): e["target"] for e in graph["edges"]}
        assert kinds[("b", "false")] == "o"  # closed → Open UEFN project
        assert kinds[("b", "true")] == "w"  # running → just wait for the listener
        start = next(n for n in graph["nodes"] if n["type"] == "uefn.game.start")
        assert start["config"]["skip_if_playing"] is True
        assert start["config"]["wait_player"] > 0


def test_check_uefn_node_never_launches(monkeypatch):
    import pytest

    from backend.automations import play, runner

    monkeypatch.setattr("frontend.window_view._uefn_running", lambda: False)
    monkeypatch.setattr(play, "_uefn_online", lambda: {"uefn_online": False, "listener_online": False, "epic_mcp_online": False})
    monkeypatch.setattr("frontend.window_view.launch_uefn_project", lambda *a, **k: pytest.fail("launched"))
    step = runner._exec_node({"id": "c", "type": "uefn.check", "config": {}}, {})
    assert step["ok"] is True
    assert step["result"]["running"] is False and step["result"]["ready"] is False
    assert runner._eval_branch({"field": "running", "op": "equals", "equals": "true"}, step["result"]) is False
    assert runner._eval_branch({"field": "running", "op": "equals", "equals": "true"}, {"running": True}) is True


def test_start_game_skips_when_already_playing(monkeypatch):
    import pytest

    from backend.automations import play, runner

    monkeypatch.setattr(play, "_uefn_online", lambda: {"uefn_online": True})
    monkeypatch.setattr(play, "_probe", lambda: {"ok": True, "playing": True, "has_player": True, "player_count": 1})
    monkeypatch.setattr("backend.tools.tester.session_play.start_game", lambda: pytest.fail("started twice"))
    step = runner._exec_node({"id": "g", "type": "uefn.game.start", "config": {"skip_if_playing": True}}, {})
    assert step["ok"] is True
    assert step["result"]["already_playing"] is True and step["result"]["playing"] is True


def test_start_game_refuses_when_uefn_offline(monkeypatch):
    from backend.automations import play, runner

    monkeypatch.setattr(play, "_uefn_online", lambda: {"uefn_online": False})
    step = runner._exec_node({"id": "g", "type": "uefn.game.start", "config": {}}, {})
    assert step["ok"] is False
    assert "Open UEFN project" in step["error"]


def test_start_game_starts_then_waits_for_player(monkeypatch):
    from backend.automations import play, runner

    calls: list[str] = []
    monkeypatch.setattr(play, "_uefn_online", lambda: {"uefn_online": True})
    monkeypatch.setattr(play, "_probe", lambda: {"ok": True, "playing": False, "has_player": False, "player_count": 0})
    monkeypatch.setattr(
        "backend.tools.tester.session_play.start_game",
        lambda: calls.append("start") or {"ok": True, "source": "listener"},
    )
    monkeypatch.setattr(
        "backend.tools.tester.session_play.wait_for_player",
        lambda timeout: calls.append(f"wait:{timeout}") or {"ok": True, "playing": True, "has_player": True, "player_count": 1},
    )
    step = runner._exec_node({"id": "g", "type": "uefn.game.start", "config": {"wait_player": 500}}, {})
    assert step["ok"] is True
    assert calls == ["start", "wait:120.0"]  # capped
    assert step["result"]["has_player"] is True and step["result"]["started"] is True


def test_stop_game_is_noop_when_nothing_plays(monkeypatch):
    import pytest

    from backend.automations import play, runner

    monkeypatch.setattr(play, "_uefn_online", lambda: {"uefn_online": True})
    monkeypatch.setattr(play, "_probe", lambda: {"ok": True, "playing": False})
    monkeypatch.setattr("backend.bridge.send_command", lambda *a, **k: pytest.fail("stop_pie sent"))
    step = runner._exec_node({"id": "x", "type": "uefn.game.stop", "config": {}}, {})
    assert step["ok"] is True and step["result"]["already_stopped"] is True


def test_expect_log_continues_from_previous_offset(monkeypatch):
    from backend.automations import runner

    seen: dict[str, object] = {}

    def fake_expect(regex, timeout, since_offset):
        seen.update(regex=regex, timeout=timeout, since=since_offset)
        return {"ok": True, "log_matches": ["[Tycoon] purchase: dropper"], "count": 1, "log_offset": 940}

    monkeypatch.setattr("backend.tools.tester.session_play.expect_log", fake_expect)
    step = runner._exec_node(
        {"id": "e", "type": "uefn.log.expect", "config": {"regex": r"\[Tycoon\].*purchase", "timeout": 999}},
        {"log_offset": 512},
    )
    assert step["ok"] is True
    assert seen == {"regex": r"\[Tycoon\].*purchase", "timeout": 120.0, "since": 512}
    assert step["result"]["log_offset"] == 940
    monkeypatch.setattr(
        "backend.tools.tester.session_play.expect_log",
        lambda *a: {"ok": False, "error": "log pattern not seen", "log_matches": [], "count": 0, "log_offset": 3},
    )
    failed = runner._exec_node({"id": "e", "type": "uefn.log.expect", "config": {"regex": "nope"}}, {})
    assert failed["ok"] is False and "nope" in failed["error"]


def test_tool_call_exposes_parsed_json_for_branches(monkeypatch):
    import json

    from backend.automations import runner
    from backend.server import mcp

    class FakeTool:
        fn = staticmethod(lambda **kw: json.dumps({"compile": {"numErrors": 2}, "hints": [{"code": "3506"}]}))

    monkeypatch.setattr(mcp._tool_manager, "get_tool", lambda name: FakeTool if name == "workspace_compile_verse" else None)
    step = runner._exec_node({"id": "v", "type": "tool.call", "config": {"name": "workspace_compile_verse", "arguments": {}}}, {})
    assert step["ok"] is True
    assert step["result"]["data"]["compile"]["numErrors"] == 2
    ctx: dict = {}
    ctx.update({k: v for k, v in step["result"].items() if k != "ok"})
    assert runner._eval_branch({"field": "data.hints", "op": "exists"}, ctx) is True
    assert runner._eval_branch({"field": "data.hints", "op": "exists"}, {"data": {"compile": {}}}) is False
