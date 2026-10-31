from __future__ import annotations

from backend.uefn_plugins import host
from backend.uefn_plugins.reference_sources import list_plugin_references


def test_plugin_items_become_references(monkeypatch):
    sources = [
        {"id": "boards", "rpc": "boards_for_chat", "panel": "Board", "label": "Kanban", "icon": "data:image/svg+xml;base64,AA", "plugin_id": "kanban"},
        {"id": "broken", "rpc": "boom", "panel": "main", "plugin_id": "other"},
        {"id": "wrapped", "rpc": "wrapped", "panel": "main", "trigger": "@", "plugin_id": "people"},
    ]
    replies = {
        "boards_for_chat": [
            {"id": "b123", "label": "Limo backlog", "description": "12 cards"},
            {"id": "bad id!", "label": "Dropped: the id can't go in a link"},
            {"id": "b9", "name": "Art", "icon": "🎨"},
        ],
        "wrapped": {"items": [{"id": "p1", "label": "Mr Apple"}]},
    }

    def call(_pid, method, _params):
        if method == "boom":
            raise RuntimeError("plugin crashed")
        return replies[method]

    monkeypatch.setattr(host, "get_reference_sources", lambda: sources)
    monkeypatch.setattr(host, "call_panel_rpc", call)

    rows = list_plugin_references()
    assert [r["label"] for r in rows] == ["Limo backlog", "Art", "Mr Apple"]
    first = rows[0]
    assert first["href"] == "plugin:kanban/board:b123"
    assert first["group"] == "Kanban" and first["trigger"] == "/"
    assert first["description"] == "12 cards"
    assert first["icon_url"].startswith("data:image/")  # the plugin's icon when the item has none
    assert rows[1]["icon_url"] == "🎨"
    assert rows[2]["trigger"] == "@" and rows[2]["group"] == "people"
