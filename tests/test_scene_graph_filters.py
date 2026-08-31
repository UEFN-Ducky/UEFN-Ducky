"""Unit tests for Scene Graph listing/transform helpers (no UEFN / unreal)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_FILTERS = (
    Path(__file__).resolve().parents[1]
    / "ducky_app"
    / "uefn_listener"
    / "listener"
    / "registry"
    / "scene_graph_filters.py"
)
_spec = importlib.util.spec_from_file_location("scene_graph_filters", _FILTERS)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

is_junk_entity_name = _mod.is_junk_entity_name
is_proxy_shadow_path = _mod.is_proxy_shadow_path
spatial_to_unreal_xyz = _mod.spatial_to_unreal_xyz


def test_junk_names() -> None:
    assert is_junk_entity_name("Default__entity")
    assert is_junk_entity_name("TRASH_foo")
    assert is_junk_entity_name("REINST_EP_SolarSystem_C_1")
    assert not is_junk_entity_name("EP_SolarSystem_sku7neetnqqk2_1436053018")


def test_proxy_shadow_paths() -> None:
    assert is_proxy_shadow_path(
        "/catland/catland.catland:PersistentLevel.EntityProxyActor_27.EP_SolarSystem_x"
    )
    assert not is_proxy_shadow_path(
        "/catland/catland.catland:PersistentLevel.LevelEntity.LevelEntity.EP_SolarSystem_x"
    )


def test_spatial_to_unreal() -> None:
    assert spatial_to_unreal_xyz([123152.0, 7048.0, 156.0]) == (123152.0, -7048.0, 156.0)
    with pytest.raises(ValueError):
        spatial_to_unreal_xyz([1.0, 2.0])
