"""Content → UEFN package path helpers for asset deletes."""

from frontend.ui_web.project_files import content_disk_rel, content_package_rel


def test_content_disk_rel_strips_prefix() -> None:
    assert content_disk_rel("Content/Animal-Cat-FreeBundle/Materials/MI_Cat_01.uasset") == (
        "Animal-Cat-FreeBundle/Materials/MI_Cat_01.uasset"
    )
    assert content_disk_rel("content/Foo") == "Foo"


def test_content_package_rel_strips_asset_suffix() -> None:
    assert content_package_rel("Content/Animal-Cat-FreeBundle/Materials/MI_Cat_01.uasset") == (
        "Animal-Cat-FreeBundle/Materials/MI_Cat_01"
    )
    assert content_package_rel("Content/Animal-Cat-FreeBundle") == "Animal-Cat-FreeBundle"
    assert content_package_rel("Content/Maps/Island.umap") == "Maps/Island"
