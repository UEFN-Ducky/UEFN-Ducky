"""Plugin-owned skill packs must not be exportable."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.skill import export_skill_pack_to_zip, save_pack_manifest


def test_export_plugin_owned_skill_blocked(tmp_path: Path) -> None:
    with patch("backend.skill.plugin_owner_for_skill", return_value="materials"):
        with pytest.raises(PermissionError, match="plugin-owned"):
            export_skill_pack_to_zip("materials", tmp_path / "out.ducky-skill-pack")


def test_save_pack_manifest_plugin_owned_blocked() -> None:
    with patch("backend.skill.plugin_owner_for_skill", return_value="materials"):
        with pytest.raises(PermissionError, match="plugin-owned"):
            save_pack_manifest("materials", {"label": "stolen"})
