"""Guard: domain editor tools register via Store plugins, not tools/__init__."""

from __future__ import annotations

from pathlib import Path


def test_tools_init_does_not_import_domain_modules() -> None:
    init = Path(__file__).resolve().parents[1] / "tools" / "__init__.py"
    text = init.read_text(encoding="utf-8")
    forbidden = (
        "actors",
        "niagara",
        "verse",
        "testing",
        "scene_graph",
        "data_tables",
        "modeling",
        "animation_retarget",
        "level_design",
    )
    for name in forbidden:
        assert f"import {name}" not in text and f"import backend.tools.{name}" not in text, name


def test_builtin_groups_ducky_only() -> None:
    from backend.builtin_toolsets import BUILTIN_GROUP_IDS, BRIDGE_TOOLS, BUILTIN_DUCKY

    assert BUILTIN_GROUP_IDS == (BUILTIN_DUCKY,)
    assert "ping" in BRIDGE_TOOLS
