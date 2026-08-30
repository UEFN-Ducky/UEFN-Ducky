"""Frozen/dynamic split, sticky tools, and prefix-overlap fingerprints."""

from __future__ import annotations

from types import SimpleNamespace

from backend.agent.prompt_cache import (
    LIVE_CONTEXT_PREFIX,
    build_cache_payload,
    cache_prefix_fingerprint,
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


def test_overlap_fingerprint_hashes_tool_schemas_not_just_names():
    frozen = "FROZEN"
    msgs = [{"role": "user", "content": "hi"}]
    tools_a = [{"name": "read", "description": "old"}]
    tools_b = [{"name": "read", "description": "new"}]
    assert cache_prefix_fingerprint(frozen, tools_a, msgs) != cache_prefix_fingerprint(
        frozen, tools_b, msgs
    )
    same = cache_prefix_fingerprint(frozen, tools_a, msgs)
    assert same == cache_prefix_fingerprint(frozen, tools_a, msgs)


def test_overlap_ignores_live_tail_and_cache_control():
    frozen = "FROZEN"
    tools = [{"name": "read", "cache_control": {"type": "ephemeral"}}]
    hist = [{"role": "user", "content": "hi"}]
    with_tail = hist + [{"role": "user", "content": f"{LIVE_CONTEXT_PREFIX}\nnow"}]
    marked = [{"role": "user", "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}]}]
    a = cache_prefix_fingerprint(frozen, tools, hist)
    b = cache_prefix_fingerprint(frozen, [{"name": "read"}], with_tail)
    assert a == b
    unmarked = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    assert cache_prefix_fingerprint(frozen, [{"name": "read"}], marked) == cache_prefix_fingerprint(
        frozen, [{"name": "read"}], unmarked
    )


def test_plugin_reference_states_epoch_invariant():
    from backend.tools.panel.panel_ai_plugins import _PLUGIN_REFERENCE

    assert "Bytes before the growth point change only at an epoch." in _PLUGIN_REFERENCE


def test_consecutive_turns_share_prefix_fingerprint():
    frozen = "FROZEN"
    tools = [{"name": "read", "description": "d"}]
    t1 = [{"role": "user", "content": "one"}]
    t2 = t1 + [{"role": "assistant", "content": "ok"}, {"role": "user", "content": "two"}]
    fp1 = cache_prefix_fingerprint(frozen, tools, t1)
    fp2 = cache_prefix_fingerprint(frozen, tools, t1)
    assert fp1 == fp2
    assert cache_prefix_fingerprint(frozen, tools, t2) != fp1
