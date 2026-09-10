"""Foliage listing helpers (no Unreal)."""

from __future__ import annotations

from listener.island_placeable import (
    FOLIAGE_SOURCE_FOLDERS,
    blueprint_candidates,
    is_foliage_bake_mesh,
)


def test_asteria_bake_mesh_is_not_content_drawer():
    path = "/Game/Environments/Asteria/Foliage/Trees/OliveTree/BakeData/SM_Tree_Asteria_OliveTree_Bake"
    assert is_foliage_bake_mesh(path)


def test_creative_tree_is_not_bake_mesh():
    assert not is_foliage_bake_mesh("/Game/Creative/Environments/Apollo/Trees/SM_Tree")
    assert "/Game/Creative/Environments/" in FOLIAGE_SOURCE_FOLDERS


def test_fortnite_environments_folder_is_not_banned():
    # Users drag Fortnite assets into the level; do not treat the folder as illegal.
    assert not is_foliage_bake_mesh("/Game/Environments/Asteria/Foliage/Trees/OliveTree")


def test_blueprint_candidates_prefer_c_class():
    cands = blueprint_candidates("/Game/Creative/Environments/ApolloTrees/BP_Tree")
    assert "/Game/Creative/Environments/ApolloTrees/BP_Tree.BP_Tree_C" in cands
    already = blueprint_candidates(
        "/Game/Creative/Environments/ApolloTrees/BP_Tree.BP_Tree_C"
    )
    assert already[0].endswith("_C")
