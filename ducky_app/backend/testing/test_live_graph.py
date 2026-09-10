"""Tester live-graph ladder: cheap UEFN status, Epic first, listener second."""

from __future__ import annotations

from backend.testing import live_graph as lg


def test_uefn_online_when_epic_connected_even_if_listener_down(monkeypatch):
    monkeypatch.setattr(
        lg,
        "tester_uefn_status",
        lambda: {"epic_mcp_online": True, "listener_online": False, "uefn_online": True},
    )
    monkeypatch.setattr(
        lg,
        "try_epic_snapshot",
        lambda: {"nodes": [{"id": "/Game/Btn", "label": "Btn"}], "edges": [], "count": 1},
    )
    monkeypatch.setattr(lg, "try_listener_snapshot", lambda kwargs=None: (_ for _ in ()).throw(AssertionError("listener must not run")))
    snap, source, err = lg.snapshot_live()
    assert source == "epic"
    assert snap and snap["nodes"][0]["label"] == "Btn"
    assert err is None


def test_listener_second_when_epic_census_fails(monkeypatch):
    monkeypatch.setattr(
        lg,
        "tester_uefn_status",
        lambda: {"epic_mcp_online": True, "listener_online": True, "uefn_online": True},
    )
    monkeypatch.setattr(lg, "try_epic_snapshot", lambda: (_ for _ in ()).throw(RuntimeError("no ListPlaced")))
    monkeypatch.setattr(
        lg,
        "try_listener_snapshot",
        lambda kwargs=None: {"nodes": [{"id": "L", "label": "Live"}], "edges": [], "count": 1},
    )
    snap, source, err = lg.snapshot_live()
    assert source == "listener"
    assert snap["nodes"][0]["label"] == "Live"
    assert "UEFN MCP snapshot failed" in (err or "")


def test_status_stays_online_when_snapshot_times_out(monkeypatch):
    """The dock bug: Connections shows connected, Tester used snapshot success as health."""
    monkeypatch.setattr(
        lg,
        "tester_uefn_status",
        lambda: {"epic_mcp_online": True, "listener_online": True, "uefn_online": True},
    )
    monkeypatch.setattr(lg, "try_epic_snapshot", lambda: (_ for _ in ()).throw(TimeoutError("epic")))
    monkeypatch.setattr(lg, "try_listener_snapshot", lambda kwargs=None: (_ for _ in ()).throw(TimeoutError("2s snapshot")))

    def fake_scan(_root: str) -> dict:
        return {"nodes": [{"id": "verse://x#Card", "label": "Card", "kind": "verse_source"}], "count": 1}

    monkeypatch.setattr("backend.testing.device_sim.scan_verse_devices_from_files", fake_scan)
    monkeypatch.setattr("backend.testing.device_sim.device_graph_audit", lambda snap: None)

    listed = lg.list_tester_devices(project_root="/tmp/proj")
    assert listed["uefn_online"] is True
    assert listed["epic_mcp_online"] is True
    assert listed["listener_online"] is True
    assert listed["live"] is None
    assert listed["live_source"] is None
    assert listed["workspace"]["count"] == 1


def test_offline_does_not_call_listener_snapshot(monkeypatch):
    """Wrong probe: never POST device_graph_snapshot when health is down. Retries stay for when it is up."""
    called: list[str] = []
    monkeypatch.setattr(
        lg,
        "tester_uefn_status",
        lambda: {"epic_mcp_online": False, "listener_online": False, "uefn_online": False},
    )
    monkeypatch.setattr(lg, "try_epic_snapshot", lambda: called.append("epic") or None)
    monkeypatch.setattr(lg, "try_listener_snapshot", lambda kwargs=None: called.append("listener") or None)
    snap, source, err = lg.snapshot_live()
    assert snap is None
    assert source is None
    assert called == []
    assert err == "UEFN offline"


def test_both_down_is_actually_offline(monkeypatch):
    monkeypatch.setattr(
        lg,
        "tester_uefn_status",
        lambda: {"epic_mcp_online": False, "listener_online": False, "uefn_online": False},
    )
    monkeypatch.setattr("backend.testing.device_sim.scan_verse_devices_from_files", lambda root: {"nodes": [], "count": 0})
    monkeypatch.setattr("backend.testing.device_sim.device_graph_audit", lambda snap: None)
    listed = lg.list_tester_devices(project_root="")
    assert listed["uefn_online"] is False
    assert listed["live"] is None


def test_empty_epic_census_falls_through_to_listener(monkeypatch):
    monkeypatch.setattr(
        lg,
        "tester_uefn_status",
        lambda: {"epic_mcp_online": True, "listener_online": True, "uefn_online": True},
    )
    monkeypatch.setattr(lg, "try_epic_snapshot", lambda: {"nodes": [], "edges": [], "count": 0})
    monkeypatch.setattr(
        lg,
        "try_listener_snapshot",
        lambda kwargs=None: {"nodes": [{"id": "L", "label": "Live"}], "edges": [], "count": 1},
    )
    snap, source, err = lg.snapshot_live()
    assert source == "listener"
    assert snap["nodes"][0]["label"] == "Live"
    assert "no devices" in (err or "")


def test_coerce_snapshot_unwraps_return_value():
    wrapped = {"returnValue": {"nodes": [{"id": "a", "label": "A"}], "edges": []}}
    out = lg._coerce_snapshot(wrapped, source="epic")
    assert out is not None
    assert out["source"] == "epic"
    assert out["count"] == 1
    assert out["nodes"][0]["label"] == "A"
