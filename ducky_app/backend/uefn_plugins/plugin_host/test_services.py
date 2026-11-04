"""Header Connections probe: plugins report live MCP without touching UEFN. Plus TTS,
media makers and secret testers a plugin registers."""

from __future__ import annotations

import pytest

from backend.uefn_plugins.plugin_host import services


@pytest.fixture(autouse=True)
def _clean_probes():
    services._CONNECTION_PROBES.clear()
    services._CONNECTION_CACHE.clear()
    yield
    services._CONNECTION_PROBES.clear()
    services._CONNECTION_CACHE.clear()


def test_connection_probe_lists_enabled_plugin(monkeypatch) -> None:
    monkeypatch.setattr(services, "is_plugin_enabled", lambda pid: pid == "blender")
    services.register_connection_probe(
        "blender",
        lambda: {"online": True, "detail": "Connected · localhost:9876"},
        label="Blender MCP",
    )
    rows = services.plugin_connection_rows()
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
    assert services.plugin_connection_for_program("blender")["online"] is True


def test_disabled_plugin_is_hidden(monkeypatch) -> None:
    monkeypatch.setattr(services, "is_plugin_enabled", lambda pid: False)
    services.register_connection_probe("blender", lambda: {"online": True, "detail": "ok"})
    assert services.plugin_connection_rows() == []


def test_ok_maps_to_online(monkeypatch) -> None:
    monkeypatch.setattr(services, "is_plugin_enabled", lambda pid: True)
    services.register_connection_probe(
        "roblox-mcp",
        lambda: {"ok": True, "hint": "Ready. Call roblox_list_tools."},
        label="Roblox MCP",
        program="roblox",
    )
    [row] = services.plugin_connection_rows()
    assert row["online"] is True
    assert row["program"] == "roblox"
    assert "Ready" in row["detail"]


def test_probe_is_cached_across_header_polls(monkeypatch) -> None:
    monkeypatch.setattr(services, "is_plugin_enabled", lambda pid: True)
    hits = {"n": 0}

    def probe():
        hits["n"] += 1
        return {"online": True, "detail": f"hit {hits['n']}"}

    services.register_connection_probe("unity-mcp", probe, label="Unity MCP")
    first = services.plugin_connection_rows()
    second = services.plugin_connection_rows()
    assert hits["n"] == 1
    assert first[0]["detail"] == second[0]["detail"] == "hit 1"


def test_probe_error_shows_offline(monkeypatch) -> None:
    monkeypatch.setattr(services, "is_plugin_enabled", lambda pid: True)

    def boom():
        raise ConnectionError("open Blender")

    services.register_connection_probe("blender", boom, label="Blender MCP")
    [row] = services.plugin_connection_rows()
    assert row["online"] is False
    assert "open Blender" in row["detail"]


def test_people_sections_list_enabled_plugins_and_survive_a_broken_one(monkeypatch) -> None:
    monkeypatch.setattr(services, "is_plugin_enabled", lambda pid: pid != "off")
    services._PEOPLE_PROVIDERS.clear()
    services._PEOPLE_CACHE.clear()
    services.register_people_provider(
        "team",
        lambda: [{"id": "u1", "name": "Ana", "detail": "In BrainRot TCG", "online": True}, {"name": ""}, "junk"],
        label="Team",
    )
    services.register_people_provider("off", lambda: [{"name": "Hidden", "online": True}])
    services.register_people_provider("broken", lambda: 1 / 0, label="Broken")
    sections = {s["id"]: s for s in services.attach_plugin_connections({})["plugin_people"]}
    assert sections["team"] == {"id": "team", "label": "Team", "people": [
        {"id": "u1", "name": "Ana", "detail": "In BrainRot TCG", "online": True}]}
    assert "off" not in sections and sections["broken"]["people"] == []
    services._PEOPLE_PROVIDERS.clear()
    services._PEOPLE_CACHE.clear()


def test_a_plain_string_probe_reads_offline_words() -> None:
    row = services._normalize_connection_row("p", {"label": "P"}, "Disconnected from editor")
    assert row["online"] is False and row["label"] == "P"


def test_tts_and_media_makers_must_be_callable() -> None:
    with pytest.raises(TypeError):
        services.register_tts_synthesizer("probe-tts", "not callable")
    with pytest.raises(ValueError):
        services.register_media_generator("probe-tts", "hologram", lambda prompt: {})
