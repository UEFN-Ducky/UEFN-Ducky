"""Normalize walkthrough steps for ducky_walkthrough_run."""

from __future__ import annotations

from backend.tools.panel.panel_ui import _normalize_walkthrough_steps


def test_normalize_steps():
    out = _normalize_walkthrough_steps(
        [
            {
                "target": "settings.tab.store",
                "title": "Store",
                "body": "Open Store",
                "require_click": True,
                "navigate": "settings.store",
            },
            {"target": "settings.store.catalog", "label": "Browse"},
        ]
    )
    assert isinstance(out, list)
    assert len(out) == 2
    assert out[0]["advance"] == "require_click"
    assert out[0]["navigate"] == "settings.store"
    assert out[1]["title"] == "Browse"
    assert out[1]["advance"] == "next"


def test_normalize_rejects_empty_target():
    out = _normalize_walkthrough_steps([{"title": "x"}])
    assert isinstance(out, dict) and out.get("error")


if __name__ == "__main__":
    test_normalize_steps()
    test_normalize_rejects_empty_target()
    print("ok")
