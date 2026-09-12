"""Header Connections probe: plugins report live MCP without touching UEFN."""

from __future__ import annotations

import pytest

from backend.uefn_plugins import host


@pytest.fixture(autouse=True)
def _clean_probes():
    host._CONNECTION_PROBES.clear()
    host._CONNECTION_CACHE.clear()
    yield
    host._CONNECTION_PROBES.clear()
    host._CONNECTION_CACHE.clear()


def test_connection_probe_lists_enabled_plugin(monkeypatch) -> None:
    monkeypatch.setattr(host, "is_plugin_enabled", lambda pid: pid == "blender")
    host.register_connection_probe(
        "blender",
        lambda: {"online": True, "detail": "Connected · localhost:9876"},
        label="Blender MCP",
    )
    rows = host.plugin_connection_rows()
    assert rows == [
        {
            "id": "blender",
            "program": "blender",
            "label": "Blender MCP",
            "online": True,
            "warn": False,
            "detail": "Connected · localhost:9876",
        }
    ]
    assert host.plugin_connection_for_program("blender")["online"] is True


def test_disabled_plugin_is_hidden(monkeypatch) -> None:
    monkeypatch.setattr(host, "is_plugin_enabled", lambda pid: False)
    host.register_connection_probe("blender", lambda: {"online": True, "detail": "ok"})
    assert host.plugin_connection_rows() == []


def test_ok_maps_to_online(monkeypatch) -> None:
    monkeypatch.setattr(host, "is_plugin_enabled", lambda pid: True)
    host.register_connection_probe(
        "roblox-mcp",
        lambda: {"ok": True, "hint": "Ready. Call roblox_list_tools."},
        label="Roblox MCP",
        program="roblox",
    )
    [row] = host.plugin_connection_rows()
    assert row["online"] is True
    assert row["program"] == "roblox"
    assert "Ready" in row["detail"]


def test_probe_is_cached_across_header_polls(monkeypatch) -> None:
    monkeypatch.setattr(host, "is_plugin_enabled", lambda pid: True)
    hits = {"n": 0}

    def probe():
        hits["n"] += 1
        return {"online": True, "detail": f"hit {hits['n']}"}

    host.register_connection_probe("unity-mcp", probe, label="Unity MCP")
    first = host.plugin_connection_rows()
    second = host.plugin_connection_rows()
    assert hits["n"] == 1
    assert first[0]["detail"] == second[0]["detail"] == "hit 1"


def test_probe_error_shows_offline(monkeypatch) -> None:
    monkeypatch.setattr(host, "is_plugin_enabled", lambda pid: True)

    def boom():
        raise ConnectionError("open Blender")

    host.register_connection_probe("blender", boom, label="Blender MCP")
    [row] = host.plugin_connection_rows()
    assert row["online"] is False
    assert "open Blender" in row["detail"]
