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
    assert {"start.manual", "start.cron", "ducky.prompt", "ducky.spawn", "flow.wait", "flow.branch", "tool.call"} <= types


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
