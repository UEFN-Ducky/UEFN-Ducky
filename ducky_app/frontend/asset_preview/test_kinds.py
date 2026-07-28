"""Tests for asset preview kind classification."""

from frontend.asset_preview.kinds import (
    clean_listener_error,
    guess_preview_kind,
    supports_material_preview,
    supports_mesh_preview,
    supports_texture_preview,
)


def test_guess_preview_kind_by_class():
    assert guess_preview_kind("x.uasset", "StaticMesh") == "static_mesh"
    assert guess_preview_kind("x.uasset", "Material") == "material"
    assert guess_preview_kind("x.uasset", "Texture2D") == "texture"
    assert guess_preview_kind("x.uasset", "NiagaraEmitter") == "niagara"


def test_guess_preview_kind_by_path():
    assert guess_preview_kind("Content/PickupSet/Materials/M_Ground.uasset") == "material"
    assert guess_preview_kind("Content/Textures/T_Bronze_2.uasset") == "texture"
    assert guess_preview_kind("Content/PickupSet/Fx/NiagaraEmitters/Ne_Aura.uasset") == "niagara"
    assert guess_preview_kind("Content/Meshes/SM_Box.uasset") == "static_mesh"


def test_object_redirector_falls_through_to_path():
    assert (
        guess_preview_kind(
            "Content/sA_PickupSet_1/Models/SM_Pickup_Gem.uasset",
            "ObjectRedirector",
        )
        == "static_mesh"
    )
    assert supports_mesh_preview(
        "Content/sA_PickupSet_1/Models/SM_Pickup_Gem.uasset",
        "ObjectRedirector",
    )


def test_supports_flags():
    assert supports_mesh_preview("Content/Meshes/SM_Box.uasset") is True
    assert supports_mesh_preview("Content/Materials/M_Ground.uasset") is False
    assert supports_material_preview("Content/Materials/M_Ground.uasset") is True
    assert supports_material_preview("Content/Fx/Ne_Aura.uasset") is False
    assert supports_texture_preview("Content/Textures/T_Bronze_2.uasset") is True
    assert supports_texture_preview("Content/Textures/T_Bronze_2.uasset", "Texture2D") is True
    assert supports_mesh_preview("Content/Textures/T_Bronze_2.uasset", "Texture2D") is False


def test_clean_listener_error():
    raw = (
        "UEFN command 'preview_static_mesh' failed: Not a StaticMesh (got Material).\n"
        "Traceback (most recent call last):\n  File ..."
    )
    assert clean_listener_error(raw) == "Not a StaticMesh (got Material)."
