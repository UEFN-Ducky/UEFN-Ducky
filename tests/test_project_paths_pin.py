"""Unit tests for content_root path pinning (no Unreal runtime)."""

from __future__ import annotations

import sys
import types
import unittest

# listener.project_paths imports unreal at module load — stub it for host tests.
if "unreal" not in sys.modules:
    sys.modules["unreal"] = types.ModuleType("unreal")

# Listener package lives under ducky_app/uefn_listener/
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1] / "ducky_app" / "uefn_listener"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from listener.project_paths import pin_asset_path_string, pin_folder_string  # noqa: E402


class PinFolderTests(unittest.TestCase):
    def test_empty_defaults_to_leaf(self):
        self.assertEqual(pin_folder_string("", "/VideoTest", default_leaf="Materials"), "/VideoTest/Materials")

    def test_retarget_game_materials_city(self):
        self.assertEqual(
            pin_folder_string("/Game/Materials/City", "/VideoTest/", default_leaf="Materials"),
            "/VideoTest/Materials/City",
        )

    def test_retarget_game_vfx(self):
        self.assertEqual(
            pin_folder_string("/Game/VFX", "/VideoTest", default_leaf="VFX"),
            "/VideoTest/VFX",
        )

    def test_already_under_project(self):
        self.assertEqual(
            pin_folder_string("/VideoTest/Materials/City", "/VideoTest", default_leaf="Materials"),
            "/VideoTest/Materials/City",
        )

    def test_relative(self):
        self.assertEqual(
            pin_folder_string("Materials/City", "/VideoTest", default_leaf="Materials"),
            "/VideoTest/Materials/City",
        )

    def test_engine_rejected(self):
        with self.assertRaises(ValueError):
            pin_folder_string("/Engine/EngineMaterials", "/VideoTest")

    def test_other_mount_rejected(self):
        with self.assertRaises(ValueError):
            pin_folder_string("/OtherIsland/Materials", "/VideoTest")

    def test_empty_leaf_stays_at_root(self):
        self.assertEqual(pin_folder_string("", "/VideoTest", default_leaf=""), "/VideoTest")
        self.assertEqual(pin_folder_string("/Game/", "/VideoTest", default_leaf=""), "/VideoTest")

    def test_pin_asset_path_retargets_game(self):
        self.assertEqual(
            pin_asset_path_string("/Game/Materials/City/M_City_Wall", "/VideoTest"),
            "/VideoTest/Materials/City/M_City_Wall",
        )
        self.assertEqual(
            pin_asset_path_string("/Game/Materials/M_X.M_X", "/VideoTest"),
            "/VideoTest/Materials/M_X",
        )


if __name__ == "__main__":
    unittest.main()
