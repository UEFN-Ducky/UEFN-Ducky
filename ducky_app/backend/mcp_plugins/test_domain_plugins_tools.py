"""Guard: domain editor tools register via Store plugins, not tools/__init__."""

from __future__ import annotations

from pathlib import Path


def test_tools_init_does_not_import_domain_modules() -> None:
    init = Path(__file__).resolve().parents[1] / "tools" / "__init__.py"
    text = init.read_text(encoding="utf-8")
    forbidden_flat = (
        "actors",
        "niagara",
        "testing",
        "scene_graph",
        "data_tables",
        "animation_retarget",
        "level_design",
    )
    for name in forbidden_flat:
        assert f"import {name}" not in text and f"import backend.tools.{name}" not in text, name
    forbidden_pkgs = (
        "backend.tools.uefn",
        "backend.tools.verse",
        "backend.tools.world",
        "backend.tools.animation",
        "backend.tools.vfx",
        "backend.tools.scene",
        "backend.tools.modeling",
        "backend.tools.tester",
        "backend.tools.integrations",
    )
    for name in forbidden_pkgs:
        assert name not in text, name


def test_builtin_groups_ducky_only() -> None:
    from backend.agent.builtin_toolsets import BUILTIN_GROUP_IDS, BRIDGE_TOOLS, BUILTIN_DUCKY

    assert BUILTIN_GROUP_IDS == (BUILTIN_DUCKY,)
    assert "ping" in BRIDGE_TOOLS
