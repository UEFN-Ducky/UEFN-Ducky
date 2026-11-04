"""Plugin composer rows keep the first valid href and drop the rest."""

from __future__ import annotations

from backend.uefn_plugins.plugin_host.ui_registry import normalize_chat_reference


def test_bad_chat_reference_href_is_dropped() -> None:
    assert (
        normalize_chat_reference(
            {"trigger": "/", "id": "x", "label": "X", "href": "javascript:alert(1)"},
            "demo",
        )
        is None
    )
    row = normalize_chat_reference(
        {"trigger": "/", "id": "verse", "label": "Verse", "href": "skill:verse", "group": "Skills"},
        "demo",
    )
    assert row is not None
    assert row["href"] == "skill:verse"
    assert row["plugin_id"] == "demo"
