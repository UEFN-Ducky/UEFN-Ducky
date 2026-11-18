"""Store catalog filtering and category rows (was a hand-run block in panel_store.py)."""

from __future__ import annotations

from backend.tools.panel.panel_store import _categories_from_items, filter_catalog_items

_ITEMS = [
    {
        "slug": "blender",
        "name": "Blender",
        "description": "3D modeling",
        "kind": "plugin",
        "category": "plugins",
        "categories": ["plugins", "3d"],
        "tags": ["mesh"],
        "state": "available",
        "icon_data_url": "data:image/png;base64,AAA",
    },
    {
        "slug": "verse-tips",
        "name": "Verse Tips",
        "description": "skill pack",
        "kind": "skill",
        "categories": ["skills"],
        "state": "installed",
    },
]


def test_filter_catalog_items_by_query_kind_category_and_state():
    hit = filter_catalog_items(_ITEMS, query="blender")
    assert [h["slug"] for h in hit] == ["blender"]
    assert "icon_data_url" not in hit[0]
    assert len(filter_catalog_items(_ITEMS, kind="skill")) == 1
    assert len(filter_catalog_items(_ITEMS, category="3d")) == 1
    assert filter_catalog_items(_ITEMS, state="update") == []


def test_categories_come_from_every_item():
    ids = {c["id"] for c in _categories_from_items(_ITEMS)}
    assert {"plugins", "skills", "3d"} <= ids
