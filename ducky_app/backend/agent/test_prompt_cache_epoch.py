"""Frozen/dynamic split and sticky tools."""

from __future__ import annotations

from types import SimpleNamespace

from backend.agent.prompt_cache import (
    build_cache_payload,
    replace_frozen_tool_names,
    sticky_frozen_tool_names,
)


def test_build_cache_payload_always_splits_when_markers_off():
    conv = SimpleNamespace(prompt_cache_snapshot=None)
    parts = {
        "runtime": "listener online",
        "memory": "## Memory\nnote",
        "plan": "## Plan\nstep 1",
        "local_slim": "",
    }
    payload = build_cache_payload(
        conv,
        parts,
        omit=frozenset({"rules", "mcp", "skill", "personality"}),
        enable_cache=False,
        freeze_enabled=True,
        prompt_cache_key="c1",
    )
    assert payload.enable_cache is False
    assert "listener online" not in payload.frozen_system
    assert "Memory" in payload.dynamic_system
    assert "Plan" in payload.dynamic_system


def test_sticky_tools_subset_unchanged_union_grows():
    conv = SimpleNamespace(
        prompt_cache_snapshot={"version": 2, "blocks": {"intro": "x"}, "tool_names": ["a", "b"]}
    )
    assert sticky_frozen_tool_names(conv, ["a"]) == ["a", "b"]
    grown = sticky_frozen_tool_names(conv, ["a", "c"])
    assert grown == ["a", "b", "c"]


def test_epoch_resets_tool_floor():
    conv = SimpleNamespace(
        prompt_cache_snapshot={"version": 2, "blocks": {"intro": "x"}, "tool_names": ["a", "b", "junk"]}
    )
    assert replace_frozen_tool_names(conv, ["a"]) == ["a"]
    assert conv.prompt_cache_snapshot["tool_names"] == ["a"]


def test_plugin_reference_is_chat_recipe_not_gateway_cache():
    from backend.tools.panel.panel_ai_plugins import _load_plugin_reference

    text = _load_plugin_reference()
    assert "ducky_plugin_list" in text
    assert "@api.tool()" in text
    assert "Never glob" in text or "never glob" in text.lower()
    assert "Gateway prompt caching" not in text
    assert "Bytes before the growth point change only at an epoch." not in text
