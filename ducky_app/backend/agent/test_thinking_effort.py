"""Host thinking-menu rules: Off=0, pass through vendor ids, no budget table."""

from __future__ import annotations

import backend.agent.thinking_effort as te


def test_normalize_off_and_passthrough() -> None:
    assert te.normalize_thinking_effort("") == "off"
    assert te.normalize_thinking_effort("NONE") == "off"
    assert te.normalize_thinking_effort("0") == "off"
    assert te.normalize_thinking_effort("xhigh") == "xhigh"
    assert te.normalize_thinking_effort("low") == "low"
    assert not hasattr(te, "EFFORT_BUDGET")


def test_ensure_injects_off_zero() -> None:
    menu = te.ensure_thinking_menu(
        {
            "lo": "Faster",
            "hi": "Smarter",
            "levels": [
                {"id": "low", "label": "Low", "thinking_tokens": 2048, "hint": "2048 thinking tokens"},
            ],
        }
    )
    assert menu is not None
    assert menu["levels"][0]["id"] == "off"
    assert menu["levels"][0]["thinking_tokens"] == 0
    assert menu["levels"][1]["id"] == "low"


def test_ensure_none_without_levels() -> None:
    assert te.ensure_thinking_menu(None) is None
    assert te.ensure_thinking_menu({}) is None
    assert te.ensure_thinking_menu({"levels": []}) is None
