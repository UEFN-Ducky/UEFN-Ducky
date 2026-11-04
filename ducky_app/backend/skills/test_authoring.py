"""Skills studio edits: create, save and delete packs and subskills; plugin-owned packs stay read-only."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.skills import authoring
from backend.skills.manifest import load_pack_manifest


@pytest.fixture()
def isolated_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def test_save_pack_manifest_plugin_owned_blocked() -> None:
    with patch("backend.skills.authoring.plugin_owner_for_skill", return_value="materials"):
        with pytest.raises(PermissionError, match="plugin-owned"):
            authoring.save_pack_manifest("materials", {"label": "stolen"})


def test_delete_plugin_owned_pack_blocked() -> None:
    with patch("backend.skills.authoring.plugin_owner_for_skill", return_value="materials"):
        with pytest.raises(ValueError, match="uninstall plugin"):
            authoring.delete_skill_pack("my_materials")


def test_create_edit_and_delete_a_pack(isolated_appdata: Path) -> None:
    del isolated_appdata
    dest = authoring.create_skill_pack("my_tips", "My Tips", "Things I learned")
    assert (dest / "SKILL.md").is_file()
    with pytest.raises(FileExistsError):
        authoring.create_skill_pack("my_tips", "Again")

    authoring.create_subskill("my_tips", "lighting", "Lighting", load_condition="lights")
    files = authoring.get_skill_pack_files("my_tips")
    assert [f["id"] for f in files["files"]] == ["core", "lighting"]
    assert files["files"][1]["load_condition"] == "lights"
    version_after_create = int(files["version"])

    authoring.save_subskill("my_tips", "lighting", "# Lighting\n\nUse warm lights.\n")
    assert "warm lights" in authoring.get_skill_pack_files("my_tips")["files"][1]["text"]
    authoring.save_skill_node("my_tips", "lighting", {"label": "Lights", "default_enabled": True})
    man = load_pack_manifest("my_tips")
    sub = next(s for s in man["subskills"] if s["id"] == "lighting")
    assert sub["label"] == "Lights" and sub["default_enabled"] is True
    assert int(man["version"]) > version_after_create

    assert authoring.delete_subskill("my_tips", "lighting") is True
    assert authoring.delete_subskill("my_tips", "lighting") is False
    with pytest.raises(ValueError):
        authoring.delete_subskill("my_tips", "core")
    graph = authoring.get_skill_pack_graph("my_tips")
    assert [n["id"] for n in graph["nodes"]] == ["core"]

    assert authoring.delete_skill_pack("my_tips") is True
    assert load_pack_manifest("my_tips") is None
