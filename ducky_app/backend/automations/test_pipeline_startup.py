
import pytest

from backend.automations import runner, uefn
from backend.automations.store import normalize_graph


@pytest.fixture
def startup(monkeypatch, tmp_path):
    from frontend import window_view
    from frontend.ui_web import project_switch

    project = tmp_path / "An Island.uefnproject"
    project.write_text("{}")
    calls = []
    monkeypatch.setattr(window_view, "_uefn_running", lambda: False)
    monkeypatch.setattr(project_switch, "switch_panel_project", lambda **kw: calls.append(("prepare", kw)) or {})
    monkeypatch.setattr(window_view, "launch_uefn_project", lambda root: calls.append(("launch", root)) or {"ok": True, "path": root})
    monkeypatch.setattr(window_view, "wait_uefn_ready", lambda **kw: calls.append(("wait", kw)) or {"ok": True, "project_match": True})
    return project, calls, window_view


def test_closed_editor_is_prepared_launched_and_awaited(startup):
    project, calls, _ = startup
    result = uefn.open_project(str(project), timeout=120)
    assert result["ok"]
    assert [call[0] for call in calls] == ["prepare", "launch", "wait"]
    assert calls[0][1] == {"path": str(project.parent), "background_deploy": False}
    assert calls[1][1] == str(project)
    assert calls[2][1] == {"root": str(project), "timeout": 120}


def test_running_project_is_reused_without_second_process(startup, monkeypatch):
    project, calls, window_view = startup
    monkeypatch.setattr(window_view, "_uefn_running", lambda: True)
    assert uefn.open_project(str(project))["already_running"]
    assert [call[0] for call in calls] == ["wait", "prepare"]


def test_wrong_running_project_is_not_replaced(startup, monkeypatch):
    project, calls, window_view = startup
    monkeypatch.setattr(window_view, "_uefn_running", lambda: True)
    monkeypatch.setattr(window_view, "wait_uefn_ready", lambda **kw: {"ok": False})
    assert not uefn.open_project(str(project))["ok"]
    assert not calls


def test_missing_project_does_not_switch_workspace_or_launch(startup):
    project, calls, _ = startup
    with pytest.raises(RuntimeError):
        uefn.open_project(str(project.parent / "missing.uefnproject"))
    assert not calls


def test_launch_node_can_return_before_ready(startup):
    project, calls, _ = startup
    step = runner._exec_node({"id": "open", "type": "uefn.launch", "config": {"project": str(project)}}, {})
    assert step["ok"]
    assert [call[0] for call in calls] == ["prepare", "launch"]


def test_readiness_failure_stops_downstream_pipeline(startup, monkeypatch):
    project, calls, window_view = startup
    monkeypatch.setattr(window_view, "wait_uefn_ready", lambda **kw: {"ok": False, "error": "not ready"})
    nodes = {"open": {"id": "open", "type": "uefn.open_project", "config": {"project": str(project)}},
             "next": {"id": "next", "type": "flow.wait", "config": {"seconds": 0}}}
    steps, ok, error, _ = runner._walk(nodes, [{"source": "open", "target": "next", "kind": "main"}], {}, ["open"])
    assert not ok and error == "not ready"
    assert [s["id"] for s in steps] == ["open"]


@pytest.mark.parametrize("width,expected", [(480, 480), (900, 720), (20, 280), (float("nan"), 320), ("bad", 320)])
def test_node_width_survives_storage_safely(width, expected):
    graph = normalize_graph({"nodes": [{"id": "a", "type": "flow.wait", "width": width}]})
    assert graph["nodes"][0]["width"] == expected


def test_project_nodes_are_in_the_catalog():
    from backend.automations.catalog import list_nodes

    types = {n["type"] for n in list_nodes()}
    assert {"uefn.open_project", "uefn.launch", "uefn.wait_ready"} <= types


def test_uefn_project_fields_use_saved_project_selector():
    from backend.automations.catalog import BUILTIN_NODES

    for node in BUILTIN_NODES:
        if node["type"] in {"uefn.open_project", "uefn.launch", "uefn.restart", "uefn.wait_ready"}:
            field = next(field for field in node["config_fields"] if field["id"] == "project")
            assert field["type"] == "project"


def test_plugin_icons_are_forwarded_once_per_plugin(monkeypatch):
    from backend.automations.catalog import list_nodes
    from backend.uefn_plugins import store
    from backend.uefn_plugins.plugin_host.testing import patch_host

    monkeypatch.setattr(store, "get_enabled_plugin_ids", lambda: ["example"])
    patch_host(monkeypatch, "get_ui_contributions", lambda: {"automations_nodes": [
        {"id": "example.a", "plugin_id": "example"}, {"id": "example.b", "plugin_id": "example"}]})
    calls = []
    monkeypatch.setattr(store, "plugin_icon_data_url", lambda pid: calls.append(pid) or "data:image/png;base64,icon")
    nodes = [n for n in list_nodes() if n.get("plugin_id") == "example"]
    assert calls == ["example"]
    assert all(n["icon"] == "data:image/png;base64,icon" for n in nodes)
