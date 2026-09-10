"""Re-export foliage listing helpers from the listener module (one source)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3] / "uefn_listener" / "listener" / "island_placeable.py"
_spec = importlib.util.spec_from_file_location("_ducky_island_placeable", _SRC)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)

FOLIAGE_SOURCE_FOLDERS = _mod.FOLIAGE_SOURCE_FOLDERS
is_foliage_bake_mesh = _mod.is_foliage_bake_mesh
blueprint_candidates = _mod.blueprint_candidates
