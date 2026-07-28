"""MCP wrapper smoke tests for area/blockout tools."""

from __future__ import annotations

from unittest.mock import patch

from backend.agent.toolsets.intents import INTENT_KEYWORDS, _LEVEL_DESIGN_TOOLS
from backend.tools import blockout_areas as tools


def test_area_tools_in_level_design_intent_set():
    required = {"area_list", "area_create", "blockout_list_presets", "blockout_layout"}
    assert required.issubset(_LEVEL_DESIGN_TOOLS)


def test_blockout_intent_matches_hub_language():
    matched: set[str] = set()
    text = "create hub blockout area and greybox store"
    for pattern, extras in INTENT_KEYWORDS:
        if pattern.search(text):
            matched |= set(extras)
    assert "area_create" in matched
    assert "blockout_layout" in matched


def test_area_create_forwards_params():
    with patch("backend.tools.blockout_areas.send_command") as send:
        send.return_value = {"ok": True, "area_id": "hub", "slot": 0}
        out = tools.area_create(area_id="hub", preset="hub", seed=7, pretty=False)
    args = send.call_args[0]
    assert args[0] == "area_create"
    assert args[1]["area_id"] == "hub"
    assert args[1]["preset"] == "hub"
    assert args[1]["seed"] == 7
    assert "ok" in out


def test_blockout_layout_forwards_params():
    with patch("backend.tools.blockout_areas.send_command") as send:
        send.return_value = {"ok": True, "placed": 8}
        out = tools.blockout_layout(
            area_id="store",
            preset="store",
            origin=[100000.0, 0.0, 0.0],
            pretty=False,
        )
    args = send.call_args[0]
    assert args[0] == "blockout_layout"
    assert args[1]["preset"] == "store"
    assert args[1]["origin"][0] == 100000.0
    assert "ok" in out
