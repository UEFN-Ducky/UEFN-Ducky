"""Workflow node catalog and Store plugin list: what the panel ships per call."""

from __future__ import annotations

import base64
import json
import time

import frontend.ui_web.panel_api  # noqa: F401 - the mixins import it first
from frontend.ui_web.panel_api_automations import PanelApiAutomationsMixin
from frontend.ui_web.panel_api_store import PanelApiStoreMixin

ICON = "data:image/png;base64," + base64.b64encode(b"\x89PNG" + b"\0" * 10_500).decode()  # ~14 KB, island-media-kit's
PLUGINS = [f"plug{i}" for i in range(40)]


def _node(pid: str, n: int) -> dict:
    return {"id": f"{pid}.n{n}", "plugin_id": pid, "label": f"Node {n}", "description": "Does a thing. " * 4,
            "config_fields": [{"id": "prompt", "label": "Prompt", "type": "string"}],
            "inputs": [{"id": "in", "label": "In", "type": "text"}], "outputs": [{"id": "out", "label": "Out", "type": "text"}]}


def test_node_catalog_sends_each_plugin_icon_once(monkeypatch) -> None:
    from backend.uefn_plugins import host, store

    monkeypatch.setattr(store, "get_enabled_plugin_ids", lambda: list(PLUGINS))
    monkeypatch.setattr(host, "get_ui_contributions", lambda: {"automations_nodes": [_node(p, n) for p in PLUGINS for n in range(30)]})
    monkeypatch.setattr(store, "plugin_icon_data_url", lambda pid: ICON)
    res = PanelApiAutomationsMixin().list_workflow_nodes()
    size = len(json.dumps(res))
    print(f"list_workflow_nodes 40 plugins x 30 nodes: {size / 1024:.0f} KB (icons sent once: {len(res['icons'])})")
    assert size < 1100 * 1024  # 40 icons x 14 KB = 560 KB of it; per node it was 16.8 MB
    plugin_nodes = [n for n in res["nodes"] if n.get("plugin_id")]
    assert len(plugin_nodes) == 1200 and all(n["icon"] == "" for n in plugin_nodes)
    assert res["icons"] == {p: ICON for p in PLUGINS}


def test_default_enabled_plugins_are_read_once_per_folder_change() -> None:
    from backend.uefn_plugins import store

    root = store.appdata_uefn_plugins_dir()
    for pid in PLUGINS:
        (root / pid).mkdir(parents=True)
        (root / pid / "plugin.json").write_text(json.dumps({"id": pid, "label": pid}), encoding="utf-8")
    first = store.get_enabled_plugin_ids()
    started = time.perf_counter()
    for _ in range(69):  # what one catalog list asked
        assert store.get_enabled_plugin_ids() == first
    assert (time.perf_counter() - started) < 0.1 and len(first) == 40
    (root / "late").mkdir()
    (root / "late" / "plugin.json").write_text(json.dumps({"id": "late"}), encoding="utf-8")
    assert "late" in store.get_enabled_plugin_ids()  # a new plugin folder is seen at once


def test_store_list_counts_workflow_contributions() -> None:
    from backend.uefn_plugins import store

    root = store.appdata_uefn_plugins_dir()
    for pid in PLUGINS:
        (root / pid).mkdir(parents=True)
        manifest = {"id": pid, "label": pid, "contributes": {
            "settings.tabs": [{"id": "Main"}],
            "automations": {"nodes": [_node(pid, n) for n in range(30)], "templates": [{"id": f"t{n}", "graph": {"nodes": [_node(pid, 0)] * 5}} for n in range(5)]},
        }}
        (root / pid / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    res = PanelApiStoreMixin().list_uefn_plugins()
    size = len(json.dumps(res))
    print(f"list_uefn_plugins 40 plugins: {size / 1024:.0f} KB")
    assert size < 50 * 1024
    row = res["plugins"][0]
    assert row["contributes"]["automations"] == {"nodes": 30, "templates": 5}
    assert row["contributes"]["settings.tabs"] == [{"id": "Main"}]  # what the Store reads stays
    full = store.list_uefn_plugins()[0]["contributes"]["automations"]
    assert len(full["nodes"]) == 30  # backend callers keep the whole manifest
