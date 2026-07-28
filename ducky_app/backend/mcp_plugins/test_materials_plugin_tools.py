"""Materials tools live in the Store plugin — the core must not grow a stub back."""

from __future__ import annotations

from pathlib import Path


def test_no_core_materials_tool_module() -> None:
    core = Path(__file__).resolve().parents[1] / "tools" / "materials.py"
    assert not core.is_file(), "dead core materials.py stub must stay deleted"
