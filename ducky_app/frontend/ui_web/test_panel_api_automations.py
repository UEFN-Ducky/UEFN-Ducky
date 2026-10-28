"""The window's list_workflow_nodes: full catalog by default, small rows for one plugin."""

from __future__ import annotations

import json


def test_list_workflow_nodes_panel_method_filters_by_plugin(monkeypatch):
    from backend.automations import catalog
    from frontend.ui_web.panel_api_automations import PanelApiAutomationsMixin

    schema = [{"id": f"f{i}", "label": "x" * 200, "type": "string"} for i in range(30)]
    nodes = [{"id": f"googledrive.n{i}", "plugin_id": "googledrive", "config_fields": schema} for i in range(9)]
    nodes.append({"id": "other.thing", "plugin_id": "other", "config_fields": schema})
    triggers = [{"id": "gmail.received", "plugin_id": "googledrive"}]
    monkeypatch.setattr(catalog, "_contributions", lambda: (
        {"automations_nodes": nodes, "automations_triggers": triggers}, {"googledrive", "other"}))
    monkeypatch.setattr("backend.uefn_plugins.store.plugin_icon_data_url", lambda pid: "data:image/png;base64," + "A" * 200_000)
    api = PanelApiAutomationsMixin()

    small = api.list_workflow_nodes(plugin="googledrive")
    assert small["ok"] and len(small["nodes"]) == 10 and len(json.dumps(small)) < 10_000
    assert {r["id"]: r["kind"] for r in small["nodes"]}["gmail.received"] == "trigger"
    # No args: the catalog the editor always got, each plugin's icon sent once beside it.
    full = api.list_workflow_nodes()
    expected = catalog.list_nodes()
    icon = "data:image/png;base64," + "A" * 200_000
    assert full["icons"] == {"googledrive": icon, "other": icon}
    assert full["nodes"] == [{**n, "icon": ""} if n.get("plugin_id") else n for n in expected]
    assert len(json.dumps(full)) < 700_000  # two icons, not one per node (11 x 200 KB)
