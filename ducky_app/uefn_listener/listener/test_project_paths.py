"""Path pinning for imports — no Unreal required."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

sys.modules.setdefault("unreal", types.ModuleType("unreal"))

_PATH = Path(__file__).resolve().parent / "project_paths.py"
_spec = importlib.util.spec_from_file_location("project_paths_under_test", _PATH)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def test_epic_gallery_import_collapses_to_typed_folder():
    got = mod.pin_folder_string(
        "/Game/Creative/BuildingActors/Floors/Meshes/SM_X",
        "/Proj",
        default_leaf="Meshes",
    )
    assert got == "/Proj/Meshes/SM_X"


def test_mirrored_island_path_collapses_the_same_way():
    got = mod.pin_folder_string(
        "/Proj/Creative/BuildingActors/Floors/Meshes/SM_X",
        "/Proj",
        default_leaf="Meshes",
    )
    assert got == "/Proj/Meshes/SM_X"


def test_project_materials_folder_is_kept():
    got = mod.pin_folder_string("/Game/Materials/City", "/Proj", default_leaf="Materials")
    assert got == "/Proj/Materials/City"
    assert mod.pin_folder_string("/Proj/Materials/City", "/Proj") == "/Proj/Materials/City"


def test_import_leaf_follows_extension():
    assert mod.import_destination_leaf("C:/out/SM_X.fbx") == "Meshes"
    assert mod.import_destination_leaf("C:/out/T.png") == "Textures"
    assert mod.import_destination_leaf("C:/out/a.wav") == "Audio"
    assert mod.import_destination_leaf("C:/out/notes.txt") == "Imported"


def test_illegal_game_material_is_refused():
    try:
        mod.require_island_ref(
            "/Game/Packages/DS_Fortnight/SM/Materials/MI_X", root="/Proj"
        )
    except ValueError as exc:
        assert "AssetReferenceRestrictions" in str(exc)
    else:
        raise AssertionError("expected ValueError")
    assert mod.require_island_ref("/Game/Creative/Materials/M_Sidewalk", root="/Proj")
    assert mod.require_island_ref("/Proj/Materials/MI_Sidewalk", root="/Proj") == "/Proj/Materials/MI_Sidewalk"
