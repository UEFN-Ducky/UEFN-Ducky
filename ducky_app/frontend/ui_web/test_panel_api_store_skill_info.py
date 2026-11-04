"""get_skill_info is the catalog only: no skill bodies, no combined prompt."""

from __future__ import annotations

import json

import frontend.ui_web.panel_api  # noqa: F401 - panel_api_store is imported through it
from backend.mcp_plugins import store as mcp_store
from backend.skills import manifest, seeding, skill_index
from frontend.ui_web.panel_api_store import PanelApiStoreMixin


def test_skill_info_has_no_file_bodies(monkeypatch) -> None:
    def packs(*, include_text: bool = False):
        sub = {"id": "core", "label": "Core", "description": "d", "default_enabled": True}
        if include_text:
            sub["text"] = "BODY " * 100_000
        return [{"id": "uefn", "label": "UEFN", "description": "Island work", "subskills": [sub]}]

    def no_prompt(*_a, **_k):
        raise AssertionError("the combined skill prompt must not be built for the catalog")

    monkeypatch.setattr(seeding, "seed_skill_packs", lambda: None)
    monkeypatch.setattr(skill_index, "seed_skill_packs", lambda: None)
    monkeypatch.setattr(manifest, "list_skill_packs", packs)
    monkeypatch.setattr(skill_index, "build_skill_prompt", no_prompt)
    monkeypatch.setattr(mcp_store, "seed_mcp_plugins", lambda: None)

    info = PanelApiStoreMixin.get_skill_info(object())

    assert info["error"] == ""
    assert info["packs"][0]["id"] == "uefn" and info["packs"][0]["description"] == "Island work"
    assert info["default_enabled_packs"] == ["uefn"]
    assert "text" not in info["packs"][0]["subskills"][0]
    assert info["text"] == info["appdata_text"] == info["primary_text"] == ""
    assert len(json.dumps(info)) < 10_000
