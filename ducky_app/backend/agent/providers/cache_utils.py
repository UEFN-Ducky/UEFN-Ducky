"""Provider helpers for prompt cache breakpoints."""

from __future__ import annotations

from typing import Any

from backend.agent.prompt_cache import PromptCachePayload


def _plugin_pref_bool(plugin_id: str, key: str, *, fallback: bool) -> bool:
    """Read a boolean from disk-backed plugin UI prefs; missing key → fallback."""
    try:
        from frontend.ui_web.plugin_host_api import prefs_plugin_get

        prefs = prefs_plugin_get(plugin_id)
        if key in prefs and isinstance(prefs[key], bool):
            return prefs[key]
    except Exception:
        pass
    return fallback


def _plugin_owns_cache_markers(plugin_id: str) -> bool:
    """True when an enabled plugin contributes a promptCaching settings property."""
    pid = (plugin_id or "").strip().lower()
    if not pid:
        return False
    try:
        from backend.uefn_plugins.host import get_contributions

        for section in get_contributions().get("settings_sections") or []:
            if not isinstance(section, dict):
                continue
            if str(section.get("plugin_id") or "").strip().lower() != pid:
                continue
            for prop in section.get("properties") or []:
                if isinstance(prop, dict) and str(prop.get("id") or "") == "promptCaching":
                    return True
    except Exception:
        pass
    return False


def provider_cache_markers_enabled(provider: str, *, fallback: bool = True) -> bool:
    """Bill-discount markers — toggled per gateway plugin that contributes promptCaching."""
    p = (provider or "").strip().lower()
    if not _plugin_owns_cache_markers(p):
        return False
    return _plugin_pref_bool(p, "promptCaching", fallback=fallback)


def anthropic_extended_cache_ttl_enabled(*, fallback: bool = False) -> bool:
    # Pref lives on whichever gateway contributes extendedCacheTtl (Anthropic today).
    try:
        from backend.uefn_plugins.host import get_contributions

        for section in get_contributions().get("settings_sections") or []:
            if not isinstance(section, dict):
                continue
            for prop in section.get("properties") or []:
                if isinstance(prop, dict) and str(prop.get("id") or "") == "extendedCacheTtl":
                    pid = str(section.get("plugin_id") or "").strip().lower()
                    if pid:
                        return _plugin_pref_bool(pid, "extendedCacheTtl", fallback=fallback)
    except Exception:
        pass
    return fallback


def _cache_control(*, extended_ttl: bool) -> dict[str, str]:
    cc: dict[str, str] = {"type": "ephemeral"}
    if extended_ttl:
        cc["ttl"] = "1h"
    return cc


def anthropic_system_blocks(cache: PromptCachePayload | None, *, fallback_system: str) -> str | list[dict[str, Any]]:
    if cache is None or not cache.enable_cache or not cache.frozen_system.strip():
        return fallback_system
    blocks: list[dict[str, Any]] = []
    cc = _cache_control(extended_ttl=cache.anthropic_extended_ttl)
    blocks.append({"type": "text", "text": cache.frozen_system, "cache_control": cc})
    dynamic = (cache.dynamic_system or "").strip()
    if dynamic:
        blocks.append({"type": "text", "text": dynamic})
    return blocks


def anthropic_tools_with_cache(
    tools: list[dict[str, Any]],
    cache: PromptCachePayload | None,
) -> list[dict[str, Any]]:
    if not tools or cache is None or not cache.enable_cache:
        return tools
    out = [dict(t) for t in tools]
    cc = _cache_control(extended_ttl=cache.anthropic_extended_ttl)
    out[-1] = {**out[-1], "cache_control": cc}
    return out


_ANTHROPIC_LOOKBACK_BLOCKS = 20
_ANTHROPIC_MID_OFFSET = 15


def _content_block_coords(messages: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """(message_index, block_index) for every Anthropic content block (str counts as 1)."""
    coords: list[tuple[int, int]] = []
    for mi, msg in enumerate(messages):
        content = msg.get("content")
        if isinstance(content, str):
            if content.strip():
                coords.append((mi, 0))
        elif isinstance(content, list):
            for bi, block in enumerate(content):
                if isinstance(block, dict):
                    coords.append((mi, bi))
    return coords


def _mark_content_block(msg: dict[str, Any], block_index: int, cc: dict[str, str]) -> dict[str, Any]:
    last = dict(msg)
    content = last.get("content")
    if isinstance(content, str):
        if not content.strip():
            return last
        last["content"] = [{"type": "text", "text": content, "cache_control": cc}]
        return last
    if isinstance(content, list) and content:
        blocks = [dict(b) if isinstance(b, dict) else b for b in content]
        if 0 <= block_index < len(blocks) and isinstance(blocks[block_index], dict):
            blocks[block_index] = {**blocks[block_index], "cache_control": cc}
        last["content"] = blocks
    return last


def anthropic_messages_with_cache(
    messages: list[dict[str, Any]],
    cache: PromptCachePayload | None,
) -> list[dict[str, Any]]:
    """Mark last *history* block (not the Live-context tail) plus a mid breakpoint.

    System (1) and tools (2) are marked elsewhere. History (3) sits on the last
    stable history block so the volatile tail rides after it uncached. A 4th
    mid-loop marker is added only when content-block count exceeds Anthropic's
    20-block lookback (tool_use / tool_result all count), at least one block
    behind the last-history marker.
    """
    if not messages or cache is None or not cache.enable_cache:
        return messages
    from backend.agent.prompt_cache import is_live_context_content

    out = [dict(m) for m in messages]
    hist_end = len(out)
    if hist_end and is_live_context_content(out[-1].get("content")):
        hist_end -= 1
    history = out[:hist_end]
    if not history:
        return out
    coords = _content_block_coords(history)
    if not coords:
        return out
    cc = _cache_control(extended_ttl=cache.anthropic_extended_ttl)
    last_mi, last_bi = coords[-1]
    out[last_mi] = _mark_content_block(out[last_mi], last_bi, cc)
    if len(coords) > _ANTHROPIC_LOOKBACK_BLOCKS:
        mid_pos = max(0, len(coords) - 1 - _ANTHROPIC_MID_OFFSET)
        mid_mi, mid_bi = coords[mid_pos]
        if (mid_mi, mid_bi) != (last_mi, last_bi):
            out[mid_mi] = _mark_content_block(out[mid_mi], mid_bi, cc)
    return out


def openai_system_messages(cache: PromptCachePayload | None, *, fallback_system: str) -> list[dict[str, Any]]:
    if cache is None or not cache.enable_cache or not cache.frozen_system.strip():
        return [{"role": "system", "content": fallback_system}]
    messages: list[dict[str, Any]] = [{"role": "system", "content": cache.frozen_system}]
    dynamic = (cache.dynamic_system or "").strip()
    if dynamic:
        messages.append({"role": "system", "content": dynamic})
    return messages


def parse_anthropic_usage(usage: Any) -> dict[str, int]:
    if usage is None:
        return {}
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "cache_read_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        "cache_write_tokens": int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
    }


def parse_gemini_usage(usage_metadata: Any) -> dict[str, int]:
    """Parse Gemini `usage_metadata` into the shared usage dict.

    Gemini 2.5 models cache repeated prefixes implicitly (no explicit
    cache-control API); `cached_content_token_count` reports how many of the
    prompt tokens were served from that implicit cache.
    """
    if usage_metadata is None:
        return {}
    cached = int(getattr(usage_metadata, "cached_content_token_count", 0) or 0)
    return {
        "input_tokens": int(getattr(usage_metadata, "prompt_token_count", 0) or 0),
        "output_tokens": int(getattr(usage_metadata, "candidates_token_count", 0) or 0),
        "cache_read_tokens": cached,
        "cache_write_tokens": 0,
    }


def parse_openai_usage(usage: Any) -> dict[str, int]:
    if usage is None:
        return {}
    cached = 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = int(getattr(details, "cached_tokens", 0) or 0)
    return {
        "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "cache_read_tokens": cached,
        "cache_write_tokens": 0,
    }
