"""validate_uefn_asset names a folder as a folder: no Unreal required."""

from __future__ import annotations

import re
import types
from pathlib import Path

import pytest

_SRC = (Path(__file__).resolve().parent / "registry" / "assets_pipeline.py").read_text(encoding="utf-8")


def test_a_folder_path_is_answered_as_a_folder_not_as_a_missing_asset():
    lib = types.SimpleNamespace(does_asset_exist=lambda p: False,
                                does_directory_exist=lambda p: p.endswith("/Materials"))
    ns: dict = {"unreal": types.SimpleNamespace(EditorAssetLibrary=lib)}
    exec(re.search(r"\ndef validate_uefn_asset\(.*?(?=\n\ndef |\Z)", _SRC, flags=re.S).group(0), ns)
    with pytest.raises(ValueError, match=r"is a folder: validate one asset at a time\. list_assets"):
        ns["validate_uefn_asset"]("/Proj/Theater/Backdrops/Materials")
    with pytest.raises(ValueError, match="Asset not found: /Proj/Nope"):
        ns["validate_uefn_asset"]("/Proj/Nope")
