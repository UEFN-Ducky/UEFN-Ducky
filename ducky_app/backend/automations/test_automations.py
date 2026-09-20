"""Save / emit / spawn stay on the current island; cron does not steal project."""

from __future__ import annotations

from datetime import datetime
from frontend.settings import PanelSettings
from frontend.ui_web.project_chats import _use_db, create_conversation, load_conversation, project_slug


def test_save_graph_roundtrip():
    from backend.automations.store import get_automation, save_automation

    wf = save_automation(
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
    got = get_automation(wf["id"])
    assert got is not None
    assert got["name"] == "Nightly"
    assert len(got["graph"]["nodes"]) == 2
    assert got["graph"]["edges"][0]["source"] == "a"


def test_emit_runs_matching_workflows(monkeypatch):
    from backend.automations import runner
    from backend.automations.store import get_automation, save_automation

    monkeypatch.setattr(runner, "_run_message", lambda *a, **k: "run")
    save_automation(
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
    out = runner.emit_automation("email.received", {"subject": "hi"})
    assert out["ok"] is True
    assert out["runs"]
    assert out["runs"][0]["ok"] is True
    assert any(s.get("type") == "flow.wait" for s in out["runs"][0]["steps"])
    logged = get_automation(out["runs"][0]["id"])
    assert logged and logged["runs"]


def test_spawn_stays_on_current_island(tmp_path, monkeypatch):
    from backend.automations import runner
    from backend.automations.store import save_automation

    home = tmp_path / "HomeIsland"
    other = tmp_path / "OtherIsland"
    home.mkdir()
    other.mkdir()
    s = PanelSettings.load()
    s.uefn_project_root = str(home)
    s.save()
    monkeypatch.setattr(runner, "_run_message", lambda *a, **k: "run")
    wf = save_automation(
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
    result = runner.run_automation(wf["id"])
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
    from backend.automations.store import save_automation

    home = tmp_path / "IslandA"
    home.mkdir()
    s = PanelSettings.load()
    s.uefn_project_root = str(home)
    s.save()
    monkeypatch.setattr(runner, "_run_message", lambda *a, **k: "run")
    wf = save_automation(
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
    result = runner.run_automation(wf["id"], starter_id="c")
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
    from backend.automations.store import save_automation

    monkeypatch.setattr(runner, "_run_message", lambda *a, **k: "run")
    save_automation(
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
    miss = runner.emit_automation("discord.message", {"channel_id": "other", "content": "nope"})
    assert miss["ok"] is True
    assert miss["runs"] == []
    hit = runner.emit_automation("discord.message", {"channel_id": "want", "content": "yes"})
    assert hit["runs"]
    assert hit["runs"][0]["ok"] is True
    assert any(s.get("type") == "flow.wait" for s in hit["runs"][0]["steps"])


def test_kind_filter_hides_pipelines():
    from backend.automations.store import KIND_PIPELINE, get_automation, list_automations, save_automation

    auto = save_automation({"name": "AutoOne", "graph": {"nodes": [], "edges": []}})
    pipe = save_automation(
        {"name": "PipeOne", "kind": KIND_PIPELINE, "description": "draw then cut", "graph": {"nodes": [], "edges": []}}
    )
    got = get_automation(pipe["id"])
    assert got is not None
    assert got["kind"] == KIND_PIPELINE
    assert got["description"] == "draw then cut"
    autos = {r["id"] for r in list_automations()}
    pipes = {r["id"] for r in list_automations(kind=KIND_PIPELINE)}
    assert auto["id"] in autos
    assert pipe["id"] not in autos
    assert pipe["id"] in pipes
    assert auto["id"] not in pipes


def test_pipeline_catalog_nodes():
    from backend.automations.catalog import list_nodes

    auto = {n["type"] for n in list_nodes("automation")}
    pipe = {n["type"] for n in list_nodes("pipeline")}
    assert "start.chat" in pipe
    assert "pipeline.agent" in pipe
    assert "pipeline.finish" in pipe
    assert "ducky.spawn" not in pipe
    assert "start.chat" not in auto
    assert "tool.call" in auto and "tool.call" in pipe


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
    node = next(n for n in catalog.list_nodes("pipeline") if n["type"] == "openai.complete")
    model = next(f for f in node["config_fields"] if f["id"] == "model")
    size = next(f for f in node["config_fields"] if f["id"] == "size")
    assert model["type"] == "model" and model["provider"] == "openai"
    assert size["options"][0]["id"] == "1024x1024"


def test_plugin_node_systems_filter(monkeypatch):
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
    auto = {n["type"] for n in catalog.list_nodes("automation")}
    pipe = {n["type"] for n in catalog.list_nodes("pipeline")}
    assert "image.rembg" not in auto
    assert "image.rembg" in pipe


def test_run_pipeline_stamps_caller_and_finish_posts(monkeypatch):
    from backend.automations import runner
    from backend.automations.store import KIND_PIPELINE, save_automation
    from backend.workspace import identity
    from backend.workspace.identity import RunContext

    caller = create_conversation(PanelSettings.load(), "", title="Caller")
    wf = save_automation(
        {
            "name": "Return",
            "kind": KIND_PIPELINE,
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
        out = runner.run_pipeline(wf["id"], prompt="hi")
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
    from backend.automations.store import KIND_PIPELINE, save_automation

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

    def fake_wait(conv_id, text, mode, model, *, timeout_sec, parent="", attachments=None):
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
    wf = save_automation(
        {
            "name": "Draw",
            "kind": KIND_PIPELINE,
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
    out = runner.run_pipeline(wf["id"], prompt="a duck", caller_conv_id=caller.id)
    assert out["ok"] is True
    files = out.get("files") or []
    assert files
    assert any("out.png" in str(f.get("path") if isinstance(f, dict) else f) for f in files)
    worker = out.get("conv_id") or ""
    assert worker and worker != caller.id


def test_scheduler_skips_pipelines(monkeypatch):
    from backend.automations import scheduler
    from backend.automations.store import KIND_PIPELINE, save_automation

    hits: list[str] = []
    monkeypatch.setattr(
        scheduler,
        "run_automation",
        lambda wid, **k: hits.append(wid) or {"ok": True},
    )
    save_automation(
        {
            "name": "NoTick",
            "kind": KIND_PIPELINE,
            "enabled": True,
            "graph": {
                "nodes": [
                    {"id": "c", "type": "start.cron", "x": 0, "y": 0, "config": {"interval_seconds": 1}},
                ],
                "edges": [],
            },
        }
    )
    scheduler._tick()
    assert hits == []


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

    def group_create(self, name: str = "", folder_id: str = ""):
        self._n += 1
        hid = f"hub-{self._n}"
        self.created.append({"name": name, "folder_id": folder_id, "id": hid})
        return {"ok": True, "id": hid, "folder_id": folder_id or f"folder-{self._n}"}

    def group_add_member(self, group_id: str, conv_id: str, as_leader: bool = False):
        self.added.append((group_id, conv_id, as_leader))
        return {"ok": True, "leader_conv_id": conv_id if as_leader else ""}

    def group_invite(self, group_id: str, profile_id: str, model: str = "", write_allowed=None):
        self.invited.append((group_id, profile_id))
        return {"ok": True, "member": {"member_conv_id": f"mem-{profile_id}"}}


def test_pipeline_group_seats_solo_caller(monkeypatch):
    from backend.automations import runner
    from backend.automations.store import KIND_PIPELINE, save_automation
    from frontend.ui_web.project_chats import load_conversation, save_conversation

    fake = _FakeGroups()
    monkeypatch.setattr("backend.tools.panel.ducky_panel._panel_api", lambda: fake)
    caller = create_conversation(PanelSettings.load(), "", title="Solo")
    caller.parent_conv_id = ""
    save_conversation(caller)
    wf = save_automation(
        {
            "name": "Clustered",
            "kind": KIND_PIPELINE,
            "graph": {
                "nodes": [
                    {"id": "s", "type": "start.chat", "x": 0, "y": 0, "config": {}},
                    {"id": "w", "type": "flow.wait", "x": 40, "y": 0, "config": {"seconds": 0}},
                ],
                "edges": [{"source": "s", "target": "w", "kind": "main"}],
            },
        }
    )
    out = runner.run_pipeline(wf["id"], prompt="go", caller_conv_id=caller.id)
    assert out["ok"] is True
    assert fake.created
    assert fake.added and fake.added[0][1] == caller.id and fake.added[0][2] is True


def test_pipeline_group_does_not_move_grouped_caller(monkeypatch):
    from backend.automations import runner
    from backend.automations.store import KIND_PIPELINE, save_automation
    from frontend.ui_web.project_chats import load_conversation, save_conversation

    fake = _FakeGroups()
    monkeypatch.setattr("backend.tools.panel.ducky_panel._panel_api", lambda: fake)
    hub = create_conversation(PanelSettings.load(), "", title="Existing swarm")
    hub.is_group = True
    save_conversation(hub)
    caller = create_conversation(PanelSettings.load(), "", title="Member", parent_conv_id=hub.id)
    save_conversation(caller)
    wf = save_automation(
        {
            "name": "Nested",
            "kind": KIND_PIPELINE,
            "graph": {
                "nodes": [
                    {"id": "s", "type": "start.chat", "x": 0, "y": 0, "config": {}},
                    {"id": "w", "type": "flow.wait", "x": 40, "y": 0, "config": {"seconds": 0}},
                ],
                "edges": [{"source": "s", "target": "w", "kind": "main"}],
            },
        }
    )
    out = runner.run_pipeline(wf["id"], caller_conv_id=caller.id)
    assert out["ok"] is True
    assert fake.created
    assert fake.added == []
    hub_run = load_conversation(fake.created[0]["id"])
    if hub_run is not None:
        assert getattr(hub_run, "leader_conv_id", "") == caller.id


def test_pipeline_agent_forwards_image_attachments(monkeypatch, tmp_path):
    from backend.automations import runner
    from backend.automations.store import KIND_PIPELINE, save_automation

    seen = {}

    def fake_wait(conv_id, text, mode, model, *, timeout_sec, parent="", attachments=None):
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
    wf = save_automation(
        {
            "name": "See",
            "kind": KIND_PIPELINE,
            "graph": {
                "nodes": [
                    {"id": "s", "type": "start.chat", "x": 0, "y": 0, "config": {}},
                    {"id": "a", "type": "pipeline.agent", "x": 40, "y": 0, "config": {"ducky": "artist", "prompt": "look"}},
                ],
                "edges": [{"source": "s", "target": "a", "kind": "main"}],
            },
        }
    )
    out = runner.run_pipeline(wf["id"], caller_conv_id=caller.id, files=[{"path": str(img), "name": "duck.png"}])
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
    from backend.automations.store import KIND_PIPELINE, save_automation

    monkeypatch.setattr(runner, "_ensure_pipeline_group", lambda ctx, wf: None)
    monkeypatch.setattr(
        runner,
        "_pipeline_agent",
        lambda cfg, payload: {"ok": True, "result": {"text": "yes, approve this"}},
    )
    wf = save_automation(
        {
            "name": "Judge",
            "kind": KIND_PIPELINE,
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
    out = runner.run_pipeline(wf["id"])
    assert out["ok"] is True
    assert any(s.get("type") == "flow.wait" for s in out["steps"])

    monkeypatch.setattr(
        runner,
        "_pipeline_agent",
        lambda cfg, payload: {"ok": True, "result": {"text": "no reject"}},
    )
    out2 = runner.run_pipeline(wf["id"])
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
    rows = templates.list_templates(system="pipeline")
    row = next(t for t in rows if t["id"].endswith("image-to-island"))
    assert row["ready"] is False
    assert "meshy" in row["missing_plugins"]
    assert "blender" in row["missing_plugins"]


def test_foreach_runs_body_per_item():
    from backend.automations import runner
    from backend.automations.store import save_automation

    wf = save_automation(
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
    out = runner.run_automation(wf["id"], payload={"cards": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]})
    assert out["ok"] is True
    waits = [s for s in out["steps"] if s.get("type") == "flow.wait"]
    assert sum(1 for s in waits if s.get("id") == "w") == 2
    assert sum(1 for s in waits if s.get("id") == "d") == 1
    empty = runner.run_automation(wf["id"], payload={"cards": []})
    assert empty["ok"] is True
    empty_waits = [s for s in empty["steps"] if s.get("type") == "flow.wait"]
    assert sum(1 for s in empty_waits if s.get("id") == "w") == 0
    assert sum(1 for s in empty_waits if s.get("id") == "d") == 1


def test_pipeline_finish_attaches_png(tmp_path, monkeypatch):
    from backend.automations import runner
    from backend.automations.store import KIND_PIPELINE, save_automation
    from backend.workspace import identity
    from backend.workspace.identity import RunContext

    png = tmp_path / "card.png"
    png.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc``\x00\x00\x00\x04\x00\x01"
        b"\xdd\x8d\xb4\x1c\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    caller = create_conversation(PanelSettings.load(), "", title="CallerImg")
    wf = save_automation(
        {
            "name": "Img",
            "kind": KIND_PIPELINE,
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
        out = runner.run_pipeline(wf["id"], prompt="hi", files=[{"path": str(png), "name": "card.png"}])
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
    from backend.automations.store import save_automation

    events: list[dict] = []
    monkeypatch.setattr(runner, "_run_message", lambda *a, **k: "run")
    monkeypatch.setattr("frontend.ui_web.agent_modes.push_ui_event", events.append)
    wf = save_automation(
        {
            "name": "Tray",
            "enabled": True,
            "graph": {
                "nodes": [{"id": "m", "type": "start.manual", "x": 0, "y": 0, "config": {}}],
                "edges": [],
            },
        }
    )
    out = runner.run_automation(wf["id"])
    assert out["ok"] is True
    jobs = [e for e in events if e.get("type") == "background_job"]
    phases = [e.get("phase") for e in jobs]
    assert "working" in phases
    assert "done" in phases
    assert any(e.get("id") == f"graph:{wf['id']}" and e.get("phase") == "working" for e in jobs)
    assert any(str(e.get("id") or "").startswith(f"graph-run:{wf['id']}:") for e in jobs)
    assert any(e.get("type") == "graphs_changed" for e in events)
