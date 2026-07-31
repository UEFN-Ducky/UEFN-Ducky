"""Worldgen intent routing + MCP wrapper smoke tests."""

from __future__ import annotations

from unittest.mock import patch

from backend.agent.toolsets.intents import INTENT_KEYWORDS, _WORLDGEN_TOOLS
from backend.tools.world import worldgen as worldgen_tools


def test_worldgen_tools_cover_probe_terrain_foliage():
    required = {
        "worldgen_capabilities",
        "terrain_generate",
        "terrain_remove_generated",
        "foliage_list_sources",
        "foliage_scatter",
        "foliage_clear_generated",
        "landscape_list",
        "landscape_create",
        "landscape_rename",
        "landscape_sculpt",
    }
    assert required.issubset(_WORLDGEN_TOOLS)


def test_landscape_create_forwards_params():
    with patch("backend.tools.world.worldgen.send_command") as send:
        send.return_value = {"ok": False, "landscape_create": "unavailable"}
        out = worldgen_tools.landscape_create(label="level1 landscape", component_count_xy=8)
    args = send.call_args[0]
    assert args[0] == "landscape_create"
    assert args[1]["label"] == "level1 landscape"
    assert args[1]["component_count_xy"] == 8
    assert "unavailable" in out or "ok" in out


def test_worldgen_intent_matches_landscape_language():
    matched: set[str] = set()
    text = "create landscape terrain and foliage vegetation biome"
    for pattern, extras in INTENT_KEYWORDS:
        if pattern.search(text):
            matched |= set(extras)
    assert "worldgen_capabilities" in matched
    assert "terrain_generate" in matched
    assert "foliage_scatter" in matched


def test_terrain_generate_forwards_params():
    with patch("backend.tools.world.worldgen.send_command") as send:
        send.return_value = {"ok": True, "seed": 7}
        out = worldgen_tools.terrain_generate(
            location=[1.0, 2.0, 3.0],
            size_uu=12800.0,
            resolution=65,
            seed=7,
            stamps=[{"type": "hill", "x": 0, "y": 0, "radius": 1000, "height": 500}],
            pretty=False,
        )
    args = send.call_args[0]
    assert args[0] == "terrain_generate"
    assert args[1]["seed"] == 7
    assert args[1]["location"] == [1.0, 2.0, 3.0]
    assert "ok" in out


def test_foliage_scatter_forwards_params():
    with patch("backend.tools.world.worldgen.send_command") as send:
        send.return_value = {"ok": True, "instances_added": 10}
        out = worldgen_tools.foliage_scatter(
            center=[0, 0, 0],
            extent=[2000, 2000, 1000],
            sources=["/Roguelike/Mesh/Tree"],
            seed=99,
            clear_first=True,
            pretty=False,
        )
    args = send.call_args[0]
    assert args[0] == "foliage_scatter"
    assert args[1]["seed"] == 99
    assert args[1]["sources"] == ["/Roguelike/Mesh/Tree"]
    assert "ok" in out
