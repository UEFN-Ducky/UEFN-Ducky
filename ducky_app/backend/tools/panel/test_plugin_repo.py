"""plugin_repo: finding a desktop plugin's folder by its id (moved out of panel_automations)."""

from __future__ import annotations

import json

from backend.tools.panel import panel_automations, plugin_repo


def test_panel_automations_still_offers_the_same_finder() -> None:
    assert panel_automations.resolve_plugin_repo is plugin_repo.resolve_plugin_repo


def test_a_folder_matches_by_name_or_by_its_plugin_json_id(tmp_path) -> None:
    named = tmp_path / "kanban"
    named.mkdir()
    (named / "plugin.json").write_text("{}", encoding="utf-8")
    other = tmp_path / "board-plugin"
    other.mkdir()
    (other / "plugin.json").write_text(json.dumps({"id": "kanban"}), encoding="utf-8")
    bare = tmp_path / "nothing"
    bare.mkdir()
    assert plugin_repo._plugin_dir_matches(named, "kanban")
    assert plugin_repo._plugin_dir_matches(other, "kanban")
    assert not plugin_repo._plugin_dir_matches(bare, "kanban")


def test_no_plugin_named_is_an_error() -> None:
    assert plugin_repo.resolve_plugin_repo("  ", roots=[], drafts=None)["ok"] is False
