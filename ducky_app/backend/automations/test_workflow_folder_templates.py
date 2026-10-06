"""Folder templates: a template holding several workflows in nested folders, Local (files store)."""

from __future__ import annotations

import json

import pytest

from backend.automations import bundles, store, templates


@pytest.fixture(autouse=True)
def files_store(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "use_db", lambda *_: False)
    monkeypatch.setattr(store, "_files_dir", lambda: tmp_path / "workflows")
    (tmp_path / "workflows").mkdir()
    monkeypatch.setattr(store, "_announce_graphs_changed", lambda: None)
    monkeypatch.setattr(bundles, "workflow_fields", lambda: {"workflow.call": ("workflow_id",)})
    tpl_dir = tmp_path / "templates"

    def _dir(*, for_write: bool = False):
        if for_write:
            tpl_dir.mkdir(parents=True, exist_ok=True)
        return tpl_dir

    monkeypatch.setattr(templates, "_dir", _dir)
    monkeypatch.setattr(templates, "_announce_templates_changed", lambda: None)
    from backend.uefn_plugins import host, store as pstore

    contrib: dict = {"automations_templates": []}
    monkeypatch.setattr(host, "get_ui_contributions", lambda: contrib)
    monkeypatch.setattr(pstore, "get_enabled_plugin_ids", lambda: ["brainrot-tcg"])
    return contrib


def call(node_id: str, target: str) -> dict:
    return {"id": node_id, "type": "workflow.call", "x": 0, "y": 0, "config": {"workflow_id": target}}


def graph(*nodes: dict) -> dict:
    return {"nodes": list(nodes), "edges": []}


def game_folder() -> dict[str, dict]:
    """Game/{Functions/Cards, Art (empty)}: Main calls Open pack, which calls Card stats; Main calls one outside."""
    outside = store.save_workflow({"name": "Elsewhere", "graph": graph()})
    c = store.save_workflow({"name": "Card stats", "folder": "Game/Functions/Cards", "graph": graph(
        {"id": "i", "type": "flow.input", "x": 0, "y": 0, "config": {"inputs": [{"name": "card"}]}})})
    b = store.save_workflow({"name": "Open pack", "folder": "Game/Functions", "graph": graph(call("n1", c["id"]))})
    a = store.save_workflow({"name": "Main", "folder": "Game", "graph": graph(call("n1", b["id"]), call("n2", outside["id"]))})
    store.add_folder("local", "Game/Art")
    return {"a": a, "b": b, "c": c, "outside": outside}


def listed(template_id: str) -> dict:
    return next(t for t in templates.list_templates() if t["id"] == template_id)


def by_name(folder_prefix: str) -> dict[str, dict]:
    return {wf["name"]: store.get_workflow(wf["id"]) for wf in store.list_workflows()
            if (wf.get("folder") or "").startswith(folder_prefix)}


def test_save_folder_as_template_then_list_and_use_it():
    made = game_folder()
    row = templates.save_folder_template("local", "Game", "Card game kit", description="Everything for cards")
    assert row["shape"] == "bundle" and row["kind"] == "custom" and row["icon"] == "📁"
    assert row["workflow_count"] == 3 and row["folder_count"] == 3 and row["root"] == "Game"

    got = listed(row["id"])
    assert got["shape"] == "bundle" and got["workflow_count"] == 3 and got["graph"] == {"nodes": [], "edges": []}
    assert got["bundle"]["folders"] == ["Art", "Functions", "Functions/Cards"]
    assert {w["name"] for w in got["bundle"]["workflows"]} == {"Main", "Open pack", "Card stats"}

    # The source can change or go; the template keeps what it had.
    store.delete_workflow(made["c"]["id"])
    out = templates.use_template(row["id"], "local", "Projects")
    assert out["shape"] == "bundle" and out["folder"] == "Projects/Game"
    assert out["outside"] == [made["outside"]["id"]]
    folders = store.folders_of("local")
    for path in ("Projects/Game", "Projects/Game/Art", "Projects/Game/Functions/Cards"):
        assert path in folders
    copies = by_name("Projects/Game")
    assert set(copies) == {"Main", "Open pack", "Card stats"}
    assert copies["Card stats"]["folder"] == "Projects/Game/Functions/Cards"
    main = copies["Main"]
    assert out["main"] == main["id"]
    assert {n["id"]: n["config"]["workflow_id"] for n in main["graph"]["nodes"]} == {
        "n1": copies["Open pack"]["id"], "n2": made["outside"]["id"]}
    assert copies["Open pack"]["graph"]["nodes"][0]["config"]["workflow_id"] == copies["Card stats"]["id"]

    # Again: the root gets a number; name renames it.
    assert templates.use_template(row["id"], "local", "Projects")["folder"] == "Projects/Game (2)"
    assert templates.use_template(row["id"], "local", "", name="Season 2")["folder"] == "Season 2"


def test_editing_a_folder_template_keeps_its_tree_and_bad_folders_are_refused():
    game_folder()
    row = templates.save_folder_template("local", "Game")
    assert row["name"] == "Game"
    edited = templates.save_custom("Renamed", description="new words", graph={"nodes": [], "edges": []},
                                   template_id=row["id"], category="Play tests")
    assert edited["shape"] == "bundle" and edited["workflow_count"] == 3
    got = listed(row["id"])
    assert got["name"] == "Renamed" and got["category"] == "Play tests" and got["workflow_count"] == 3
    with pytest.raises(KeyError):
        templates.save_folder_template("local", "Nope")
    store.add_folder("local", "Empty")
    with pytest.raises(ValueError, match="no workflows"):
        templates.save_folder_template("local", "Empty")
    with pytest.raises(bundles.BundleError):
        templates.save_custom("Hollow", bundle={"root": "x", "workflows": []})


BRAINROT_ROW = {
    "id": "brainrot-card-art",
    "label": "BrainRot card art",
    "kind": "bundle",
    "root": "BrainRot TCG",
    "folders": ["Functions"],
    "workflows": [
        {"key": "all", "name": "BrainRot card art - all cards", "folder": "", "graph": graph(
            {"id": "each", "type": "flow.foreach", "x": 0, "y": 0, "config": {"field": "cards"}},
            call("one", "@one"),
            {"id": "art", "type": "brainrot.art", "x": 0, "y": 0, "config": {}})},
        {"key": "one", "name": "BrainRot card art - one card", "folder": "Functions", "graph": graph(
            {"id": "in", "type": "flow.input", "x": 0, "y": 0, "config": {"inputs": [{"name": "card_id"}]}})},
    ],
}


def test_plugin_bundle_template_with_at_key_calls(files_store):
    from backend.uefn_plugins import host

    parsed = host._automation_template_row(BRAINROT_ROW, "brainrot-tcg")
    nested = host._automation_template_row({"id": "nested", "label": "Nested", "bundle": {
        "root": "Kit", "workflows": BRAINROT_ROW["workflows"]}}, "brainrot-tcg")
    single = host._automation_template_row({"id": "single", "label": "Single", "graph": graph()}, "brainrot-tcg")
    assert "bundle" not in single and single["icon"] == "⚡"
    files_store["automations_templates"] = [parsed, nested, single,
                                            {**host._automation_template_row({**BRAINROT_ROW, "id": "off"}, "other"), "plugin_id": "other"}]
    rows = {t["id"]: t for t in templates.list_templates() if t.get("kind") == "plugin"}
    assert set(rows) == {"plugin:brainrot-tcg:brainrot-card-art", "plugin:brainrot-tcg:nested", "plugin:brainrot-tcg:single"}
    row = rows["plugin:brainrot-tcg:brainrot-card-art"]
    assert row["shape"] == "bundle" and row["workflow_count"] == 2 and row["icon"] == "📁" and row["root"] == "BrainRot TCG"
    assert rows["plugin:brainrot-tcg:nested"]["root"] == "Kit"
    assert rows["plugin:brainrot-tcg:single"]["shape"] == "workflow" and rows["plugin:brainrot-tcg:single"]["workflow_count"] == 1

    out = templates.use_template(row["id"], "local", "Cards")
    assert out["folder"] == "Cards/BrainRot TCG" and out["outside"] == []
    made = by_name("Cards/BrainRot TCG")
    one, every = made["BrainRot card art - one card"], made["BrainRot card art - all cards"]
    assert one["folder"] == "Cards/BrainRot TCG/Functions"
    assert out["main"] == every["id"]
    assert next(n for n in every["graph"]["nodes"] if n["id"] == "one")["config"]["workflow_id"] == one["id"]


def test_bundle_template_needing_a_plugin_is_refused(files_store, monkeypatch):
    from backend.uefn_plugins import host, store as pstore

    files_store["automations_templates"] = [host._automation_template_row({**BRAINROT_ROW, "requires_plugins": ["meshy"]}, "brainrot-tcg")]
    monkeypatch.setattr(pstore, "get_enabled_plugin_ids", lambda: ["brainrot-tcg"])
    row = listed("plugin:brainrot-tcg:brainrot-card-art")
    assert row["ready"] is False and row["missing_plugins"] == ["meshy"]
    with pytest.raises(ValueError, match="meshy"):
        templates.use_template(row["id"], "local")
    assert store.list_workflows() == []
    bad = host._automation_template_row({"id": "bad", "kind": "bundle", "workflows": [{"key": "a"}, {"key": "a"}]}, "brainrot-tcg")
    files_store["automations_templates"] = [bad]
    assert not any(t["id"] == "plugin:brainrot-tcg:bad" for t in templates.list_templates())


def test_single_workflow_templates_work_as_before():
    row = templates.save_custom("Ping", graph=graph({"id": "m", "type": "start.manual", "x": 0, "y": 0, "config": {}}))
    assert row["shape"] == "workflow" and row["graph"]["nodes"][0]["type"] == "start.manual" and "bundle" not in row
    got = listed(row["id"])
    assert got["shape"] == "workflow" and got["workflow_count"] == 1 and got["graph"]["nodes"][0]["id"] == "m"
    builtin = listed("builtin:stop-game")
    assert builtin["shape"] == "workflow" and builtin["graph"]["nodes"] and "bundle" not in builtin
    out = templates.use_template(row["id"], "local", "Tools", name="My ping")
    assert out["shape"] == "workflow"
    wf = store.get_workflow(out["workflow"]["id"])
    assert wf["name"] == "My ping" and wf["folder"] == "Tools" and wf["graph"]["nodes"][0]["type"] == "start.manual"
    with pytest.raises(LookupError):
        templates.use_template("custom:doesnotexist", "local")


def test_finder_matches_a_folder_template_by_its_workflows():
    from backend.automations.finder import find

    game_folder()
    row = templates.save_folder_template("local", "Game", "Kit")
    found = find("open pack card stats", [], templates.list_templates())
    hit = next(t for t in found["templates"] if t["id"] == row["id"])
    assert hit["shape"] == "bundle" and hit["workflow_count"] == 3


def test_panel_and_agent_tools():
    from backend.tools.panel import panel_automations as tools
    from frontend.ui_web.panel_api_automations import PanelApiAutomationsMixin

    game_folder()
    api = PanelApiAutomationsMixin()
    saved = api.save_workflow_template("Kit", "", "", "", "", "", "local", "Game")
    assert saved["ok"] is True and saved["template"]["shape"] == "bundle"
    assert api.save_workflow_template("Kit", "", "", "", "", "", "local", "Missing") == {"ok": False, "error": "folder not found"}
    single = api.save_workflow_template("One", "", "⚡", json.dumps(graph()))
    assert single["ok"] is True and single["template"]["shape"] == "workflow"

    used = api.use_workflow_template(saved["template"]["id"], "local", "Mine")
    assert used["ok"] is True and used["folder"] == "Mine/Game" and len(used["workflows"]) == 3 and used["main"]
    assert api.use_workflow_template("custom:nope") == {"ok": False, "error": "template not found"}
    one = api.use_workflow_template(single["template"]["id"], "local", "Mine")
    assert one["ok"] is True and one["workflow"]["folder"] == "Mine"

    agent = json.loads(tools.save_workflow_template(owner="local", folder="Game/Functions", category="Yours"))
    assert agent["ok"] is True and agent["template"]["name"] == "Functions" and agent["template"]["workflow_count"] == 2
    rows = {t["id"]: t for t in json.loads(tools.list_workflow_templates())["templates"]}
    assert rows[agent["template"]["id"]]["shape"] == "bundle" and rows[single["template"]["id"]]["shape"] == "workflow"
    made = json.loads(tools.create_workflow_from_template(agent["template"]["id"], name="Lib", folder="Agent"))
    assert made["ok"] is True and made["bundle"]["folder"] == "Agent/Lib" and len(made["bundle"]["workflows"]) == 2
    assert made["workflow"]["folder"] == "Agent/Lib"  # the top-level one opens
    plain = json.loads(tools.create_workflow_from_template(single["template"]["id"], folder="Agent"))
    assert plain["ok"] is True and plain["workflow"]["folder"] == "Agent" and "bundle" not in plain
    assert json.loads(tools.create_workflow_from_template("custom:nope"))["ok"] is False
    assert json.loads(tools.save_workflow_template(owner="local", folder="Nope"))["error"] == "folder not found"
