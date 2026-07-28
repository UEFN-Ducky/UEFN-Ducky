"""Niagara intent bundle includes publish-repair tools."""

from __future__ import annotations

from backend.agent.toolsets.intents import INTENT_KEYWORDS, _NIAGARA_TOOLS


def test_niagara_tools_include_validation_and_replacement():
    required = {
        "validate_uefn_asset",
        "get_dependencies",
        "duplicate_asset",
        "delete_asset",
        "save_asset",
        "open_asset_in_uefn",
        "spawn_actor",
    }
    assert required.issubset(_NIAGARA_TOOLS)


def test_niagara_intent_matches_vfx_publish_language():
    matched: set[str] = set()
    text = "fix niagara vfx publish blockers with validate_uefn_asset"
    for pattern, extras in INTENT_KEYWORDS:
        if pattern.search(text):
            matched |= set(extras)
    assert "validate_uefn_asset" in matched
    assert "duplicate_asset" in matched
    assert "niagara_capabilities" in matched
