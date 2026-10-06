"""Folder bundles: a whole folder tree of workflows as one value, Local (files store)."""

from __future__ import annotations

import pytest

from backend.automations import bundles, store


@pytest.fixture(autouse=True)
def files_store(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "use_db", lambda *_: False)
    monkeypatch.setattr(store, "_files_dir", lambda: tmp_path)
    monkeypatch.setattr(store, "_announce_graphs_changed", lambda: None)
    monkeypatch.setattr(bundles, "workflow_fields", lambda: {"workflow.call": ("workflow_id",)})


def call(node_id: str, target: str) -> dict:
    return {"id": node_id, "type": "workflow.call", "x": 0, "y": 0, "config": {"workflow_id": target}}


def graph(*nodes: dict) -> dict:
    return {"nodes": list(nodes), "edges": []}


def brainrot() -> dict[str, dict]:
    """BrainRot TCG/{Functions/Cards, Art (empty)} with A calling B, B calling C, and a call outside."""
    outside = store.save_workflow({"name": "Elsewhere", "graph": graph()})
    c = store.save_workflow({"name": "Card stats", "folder": "BrainRot TCG/Functions/Cards", "graph": graph(
        {"id": "i", "type": "flow.input", "x": 0, "y": 0, "config": {"inputs": [{"name": "card"}]}})})
    b = store.save_workflow({"name": "Open pack", "folder": "BrainRot TCG/Functions", "graph": graph(call("n1", c["id"]))})
    a = store.save_workflow({"name": "Main", "folder": "BrainRot TCG", "enabled": False,
                             "graph": graph(call("n1", b["id"]), call("n2", outside["id"]))})
    store.add_folder("local", "BrainRot TCG/Art")
    store.add_folder("local", "BrainRot TCG/Functions/Empty inside")
    return {"a": a, "b": b, "c": c, "outside": outside}


def by_name() -> dict[str, dict]:
    return {wf["name"]: store.get_workflow(wf["id"]) for wf in store.list_workflows()}


def test_export_lists_the_whole_tree_with_empty_folders():
    made = brainrot()
    bundle = bundles.export_folder("local", "BrainRot TCG")
    assert bundle["version"] == 1 and bundle["root"] == "BrainRot TCG"
    assert bundle["folders"] == ["Art", "Functions", "Functions/Cards", "Functions/Empty inside"]
    rows = {w["name"]: w for w in bundle["workflows"]}
    assert set(rows) == {"Main", "Open pack", "Card stats"}  # "Elsewhere" is outside the folder
    assert rows["Main"]["folder"] == "" and rows["Main"]["key"] == made["a"]["id"] and rows["Main"]["enabled"] is False
    assert rows["Card stats"]["folder"] == "Functions/Cards"
    with pytest.raises(KeyError):
        bundles.export_folder("local", "No such folder")


def test_round_trip_makes_new_ids_keeps_empty_folders_and_remaps_calls():
    made = brainrot()
    out = bundles.import_bundle(bundles.export_folder("local", "BrainRot TCG"), "local", "Copies")
    assert out["folder"] == "Copies/BrainRot TCG"
    assert out["outside"] == [made["outside"]["id"]]
    new = {row["key"]: row["id"] for row in out["workflows"]}
    assert set(new) == {made["a"]["id"], made["b"]["id"], made["c"]["id"]}
    assert not set(new.values()) & set(new)  # every copy has a new id
    folders = store.folders_of("local")
    for path in ("Copies/BrainRot TCG/Art", "Copies/BrainRot TCG/Functions/Empty inside", "Copies/BrainRot TCG/Functions/Cards"):
        assert path in folders
    main = store.get_workflow(new[made["a"]["id"]])
    assert main["folder"] == "Copies/BrainRot TCG" and main["enabled"] is False
    calls = {n["id"]: n["config"]["workflow_id"] for n in main["graph"]["nodes"]}
    assert calls == {"n1": new[made["b"]["id"]], "n2": made["outside"]["id"]}  # inside remapped, outside kept
    pack = store.get_workflow(new[made["b"]["id"]])
    assert pack["folder"] == "Copies/BrainRot TCG/Functions"
    assert pack["graph"]["nodes"][0]["config"]["workflow_id"] == new[made["c"]["id"]]
    # The originals are untouched.
    assert store.get_workflow(made["a"]["id"])["graph"]["nodes"][0]["config"]["workflow_id"] == made["b"]["id"]


def test_a_taken_root_gets_a_number_and_name_renames_it():
    brainrot()
    bundle = bundles.export_folder("local", "BrainRot TCG")
    assert bundles.import_bundle(bundle, "local", "")["folder"] == "BrainRot TCG (2)"
    assert bundles.import_bundle(bundle, "local", "")["folder"] == "BrainRot TCG (3)"
    assert bundles.import_bundle(bundle, "local", "Kit", name="Cards/Set")["folder"] == "Kit/Cards Set"


def test_template_style_keys_and_at_references():
    bundle = {"version": 1, "root": "Starter", "folders": ["Lib", "Empty"], "workflows": [
        {"key": "main", "name": "Main", "graph": graph(call("x", "@helper"), call("y", "helper"), call("z", "@nope"))},
        {"key": "helper", "name": "Helper", "folder": "Lib", "graph": graph()},
    ]}
    out = bundles.import_bundle(bundle, "local")
    ids = {row["key"]: row["id"] for row in out["workflows"]}
    main = store.get_workflow(ids["main"])
    assert [n["config"]["workflow_id"] for n in main["graph"]["nodes"]] == [ids["helper"], ids["helper"], "@nope"]
    assert out["outside"] == ["@nope"]
    assert "Starter/Empty" in store.folders_of("local")


def test_remap_calls_leaves_other_nodes_and_unknown_ids():
    g = graph(call("a", "old"), {"id": "b", "type": "flow.wait", "x": 0, "y": 0, "config": {"workflow_id": "old"}})
    out = bundles.remap_calls(g, {"old": "new"}, fields={"workflow.call": ("workflow_id",)})
    assert [n["config"]["workflow_id"] for n in out["nodes"]] == ["new", "old"]
    assert g["nodes"][0]["config"]["workflow_id"] == "old"  # a copy, not in place
    plugin = graph({"id": "p", "type": "acme.run_flow", "x": 0, "y": 0, "config": {"flow": "old", "other": "old"}})
    out = bundles.remap_calls(plugin, {"old": "new"}, fields={"acme.run_flow": ("flow",)})
    assert out["nodes"][0]["config"] == {"flow": "new", "other": "old"}


def test_bad_bundles_are_refused_before_anything_is_written():
    for bad, words in (([], "JSON object"), ({"version": 2, "workflows": []}, "version 2"), ({"root": "x"}, "workflows list"),
                       ({"workflows": [{"key": "a"}, {"key": "a"}]}, "share the key")):
        with pytest.raises(bundles.BundleError, match=words):
            bundles.import_bundle(bad, "local")
    assert store.list_workflows() == [] and store.folders_of("local") == []


def test_a_failed_import_removes_what_it_made(monkeypatch):
    brainrot()
    before = {wf["id"] for wf in store.list_workflows()}
    folders_before = store.folders_of("local")
    real = store.save_quietly
    saved = []

    def flaky(doc, owner=""):
        if len(saved) == 2:
            raise ValueError("disk full")
        saved.append(doc["id"])
        return real(doc, owner)

    monkeypatch.setattr(store, "save_quietly", flaky)
    with pytest.raises(ValueError, match="disk full"):
        bundles.import_bundle(bundles.export_folder("local", "BrainRot TCG"), "local", "Copies")
    assert {wf["id"] for wf in store.list_workflows()} == before
    assert store.folders_of("local") == folders_before


def test_copy_and_move_inside_local():
    made = brainrot()
    out = bundles.copy_folder("local", "BrainRot TCG/Functions", "local", "Shared")
    assert out["folder"] == "Shared/Functions" and len(out["workflows"]) == 2
    assert "Shared/Functions/Empty inside" in store.folders_of("local")
    moved = bundles.copy_folder("local", "BrainRot TCG", "local", "Games", move=True)
    assert moved["folder"] == "Games/BrainRot TCG" and moved["moved"] == 3
    assert store.get_workflow(made["a"]["id"])["folder"] == "Games/BrainRot TCG"  # same ids inside one owner
    assert "Games/BrainRot TCG/Art" in store.folders_of("local") and "BrainRot TCG" not in store.folders_of("local")
    with pytest.raises(ValueError):
        bundles.copy_folder("local", "Games", "local", "Games/BrainRot TCG", move=True)
    with pytest.raises(ValueError, match="database store"):
        bundles.copy_folder("local", "Games", "teamT")


def test_empty_folders_persist_and_follow_renames():
    store.add_folder("local", "Plans/Later")
    wf = store.save_workflow({"name": "Now", "folder": "Plans", "graph": graph()})
    assert store.folders_of("local") == ["Plans", "Plans/Later"]
    store.move_folder("local", "Plans", "Ideas")
    assert store.folders_of("local") == ["Ideas", "Ideas/Later"]
    store.set_folder(wf["id"], "")
    assert store.folders_of("local") == ["Ideas", "Ideas/Later"]  # the folder it left stays
    store.move_folder("local", "Ideas/Later", "Ideas")  # remove: into its parent
    assert store.folders_of("local") == ["Ideas"]
    assert store.drop_folders("local", "Ideas") is True and store.folders_of("local") == []
    assert all(wf["name"] != "_folders" for wf in store.list_workflows())
    with pytest.raises(ValueError):
        store.save_workflow({"id": "_folders", "name": "x", "graph": graph()})


def test_panel_and_agent_tools():
    import json

    from backend.tools.panel import panel_automations as tools
    from frontend.ui_web.panel_api_automations import PanelApiAutomationsMixin

    made = brainrot()
    api = PanelApiAutomationsMixin()
    assert api.add_workflow_folder("local", "Inbox/Later")["folders"][:2] == ["BrainRot TCG", "BrainRot TCG/Art"]
    assert api.add_workflow_folder("local", " / ")["ok"] is False
    bundle = api.export_workflow_folder("local", "BrainRot TCG")["bundle"]
    assert api.export_workflow_folder("local", "Missing") == {"ok": False, "error": "folder not found"}
    out = api.import_workflow_bundle(bundle, "local", "", "Copy")
    assert out["ok"] is True and out["folder"] == "Copy" and len(out["workflows"]) == 3
    assert api.import_workflow_bundle({"workflows": "no"}, "local")["ok"] is False
    copied = api.copy_workflow_folder("local", "BrainRot TCG/Functions", "local", "Inbox")
    assert copied["ok"] is True and copied["folder"] == "Inbox/Functions"
    assert api.copy_workflow_folder("local", "Nope", "local")["ok"] is False

    exported = json.loads(tools.export_workflow_folder("local", "BrainRot TCG"))
    assert exported["ok"] is True and exported["bundle"]["folders"] == bundle["folders"]
    imported = json.loads(tools.import_workflow_bundle(json.dumps(bundle), "local", "Agent"))
    assert imported["ok"] is True and imported["folder"] == "Agent/BrainRot TCG"
    assert json.loads(tools.add_workflow_folder("local", "Agent/Empty"))["ok"] is True
    moved = json.loads(tools.copy_workflow_folder("local", "Agent", "local", "Archive", move=True))
    assert moved["ok"] is True and moved["folder"] == "Archive/Agent"
    assert "Archive/Agent/Empty" in store.folders_of("local")
    assert store.get_workflow(made["a"]["id"])["folder"] == "BrainRot TCG"
