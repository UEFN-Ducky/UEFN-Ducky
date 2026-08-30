"""Tests for provider prompt-cache breakpoint helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from backend.agent.prompt_cache import LIVE_CONTEXT_PREFIX, PromptCachePayload
from backend.agent.providers.cache_utils import (
    anthropic_messages_with_cache,
    anthropic_tools_with_cache,
    parse_gemini_usage,
    provider_cache_markers_enabled,
)


def test_provider_cache_markers_require_contribution():
    with patch(
        "backend.agent.providers.cache_utils._plugin_owns_cache_markers",
        side_effect=lambda pid: pid in {"anthropic", "openai"},
    ):
        assert provider_cache_markers_enabled("gemini", fallback=True) is False
        assert provider_cache_markers_enabled("ollama", fallback=True) is False
        assert provider_cache_markers_enabled("anthropic", fallback=True) is True
        assert provider_cache_markers_enabled("openai", fallback=False) is False


def _cache(*, enabled: bool = True, extended_ttl: bool = False) -> PromptCachePayload:
    return PromptCachePayload(
        frozen_system="frozen",
        dynamic_system="dynamic",
        enable_cache=enabled,
        anthropic_extended_ttl=extended_ttl,
    )


def test_anthropic_messages_with_cache_marks_last_string_message():
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "second"},
        {"role": "user", "content": "latest question"},
    ]
    out = anthropic_messages_with_cache(messages, _cache())
    assert out[0] == {"role": "user", "content": "first"}
    assert out[1] == {"role": "assistant", "content": "second"}
    assert out[2]["content"] == [
        {"type": "text", "text": "latest question", "cache_control": {"type": "ephemeral"}}
    ]


def test_anthropic_messages_with_cache_marks_last_block_of_list_content():
    messages = [
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "result text"}],
        }
    ]
    out = anthropic_messages_with_cache(messages, _cache())
    assert out[0]["content"] == [
        {
            "type": "tool_result",
            "tool_use_id": "t1",
            "content": "result text",
            "cache_control": {"type": "ephemeral"},
        }
    ]


def test_anthropic_messages_with_cache_extended_ttl():
    messages = [{"role": "user", "content": "hello"}]
    out = anthropic_messages_with_cache(messages, _cache(extended_ttl=True))
    assert out[0]["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_anthropic_messages_with_cache_noop_when_disabled_or_empty():
    messages = [{"role": "user", "content": "hello"}]
    assert anthropic_messages_with_cache(messages, None) == messages
    assert anthropic_messages_with_cache(messages, _cache(enabled=False)) == messages
    assert anthropic_messages_with_cache([], _cache()) == []
    blank = [{"role": "user", "content": "  "}]
    assert anthropic_messages_with_cache(blank, _cache()) == blank


def test_anthropic_tools_with_cache_marks_last_tool_only():
    tools = [{"name": "a"}, {"name": "b"}]
    out = anthropic_tools_with_cache(tools, _cache())
    assert "cache_control" not in out[0]
    assert out[1]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_marks_last_history_not_live_tail():
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "second"},
        {"role": "user", "content": f"{LIVE_CONTEXT_PREFIX}\nplan tick"},
    ]
    out = anthropic_messages_with_cache(messages, _cache())
    assert out[1]["content"] == [
        {"type": "text", "text": "second", "cache_control": {"type": "ephemeral"}}
    ]
    assert out[2]["content"] == f"{LIVE_CONTEXT_PREFIX}\nplan tick"


def test_anthropic_mid_breakpoint_only_when_over_lookback():
    short = [{"role": "user", "content": f"m{i}"} for i in range(5)]
    short_out = anthropic_messages_with_cache(short, _cache())
    marked = 0
    for m in short_out[:-1]:
        content = m.get("content")
        if isinstance(content, list) and content and content[0].get("cache_control"):
            marked += 1
    assert marked == 0

    long = [{"role": "user", "content": [{"type": "text", "text": f"b{i}"}]} for i in range(22)]
    long.append({"role": "user", "content": f"{LIVE_CONTEXT_PREFIX}\ntail"})
    long_out = anthropic_messages_with_cache(long, _cache())
    hist = long_out[:-1]
    marked_idxs = []
    for i, m in enumerate(hist):
        content = m.get("content")
        if isinstance(content, list) and content and content[-1].get("cache_control"):
            marked_idxs.append(i)
    assert len(marked_idxs) == 2
    assert marked_idxs[-1] == 21
    assert marked_idxs[0] != marked_idxs[-1]
    assert marked_idxs[-1] - marked_idxs[0] >= 1


def test_parse_gemini_usage_reads_cached_content_tokens():
    usage_metadata = SimpleNamespace(
        prompt_token_count=1200,
        candidates_token_count=340,
        cached_content_token_count=900,
    )
    parsed = parse_gemini_usage(usage_metadata)
    assert parsed == {
        "input_tokens": 1200,
        "output_tokens": 340,
        "cache_read_tokens": 900,
        "cache_write_tokens": 0,
    }


def test_parse_gemini_usage_handles_none_and_missing_fields():
    assert parse_gemini_usage(None) == {}
    parsed = parse_gemini_usage(SimpleNamespace())
    assert parsed == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
