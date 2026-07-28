"""Per-API-call token accounting for conversations."""

from __future__ import annotations

import time
from typing import Any

_MAX_CALLS = 500


def empty_token_usage() -> dict[str, Any]:
    return {
        "total_input": 0,
        "total_output": 0,
        "total_cache_read": 0,
        "total_cache_write": 0,
        "calls": [],
    }


def normalize_token_usage(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return empty_token_usage()
    calls = raw.get("calls")
    if not isinstance(calls, list):
        calls = []
    cleaned: list[dict[str, Any]] = []
    for entry in calls:
        if not isinstance(entry, dict):
            continue
        cost = entry.get("cost_usd")
        cleaned.append(
            {
                "ts": float(entry.get("ts") or 0),
                "step": int(entry.get("step") or 0),
                "input_tokens": max(0, int(entry.get("input_tokens") or 0)),
                "output_tokens": max(0, int(entry.get("output_tokens") or 0)),
                "cache_read_tokens": max(0, int(entry.get("cache_read_tokens") or 0)),
                "cache_write_tokens": max(0, int(entry.get("cache_write_tokens") or 0)),
                "provider": str(entry.get("provider") or ""),
                "model": str(entry.get("model") or ""),
                # Authoritative cost when the backend reported one (Claude Code);
                # None means "derive from pricing tables" downstream.
                "cost_usd": float(cost) if isinstance(cost, (int, float)) else None,
            }
        )
    return {
        "total_input": max(0, int(raw.get("total_input") or 0)),
        "total_output": max(0, int(raw.get("total_output") or 0)),
        "total_cache_read": max(0, int(raw.get("total_cache_read") or 0)),
        "total_cache_write": max(0, int(raw.get("total_cache_write") or 0)),
        "calls": cleaned[-_MAX_CALLS:],
    }


def clear_token_usage(conv: Any) -> None:
    conv.token_usage = empty_token_usage()


def record_api_call(
    conv: Any,
    *,
    input_tokens: int,
    output_tokens: int,
    step: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    provider: str = "",
    model: str = "",
    cost_usd: float | None = None,
) -> dict[str, Any]:
    """Append one LLM API call to the conversation usage log.

    provider/model are recorded per call so cost stays correct after the user
    switches models mid-conversation. ``cost_usd`` is the backend's authoritative
    cost when it reports one (Claude Code ``total_cost_usd``); None derives it
    from pricing tables.
    """
    inp = max(0, int(input_tokens or 0))
    out = max(0, int(output_tokens or 0))
    cache_read = max(0, int(cache_read_tokens or 0))
    cache_write = max(0, int(cache_write_tokens or 0))
    if inp == 0 and out == 0 and cache_read == 0 and cache_write == 0:
        return normalize_token_usage(getattr(conv, "token_usage", None))

    usage = normalize_token_usage(getattr(conv, "token_usage", None))
    usage["calls"].append(
        {
            "ts": time.time(),
            "step": max(0, int(step or 0)),
            "input_tokens": inp,
            "output_tokens": out,
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
            "provider": str(provider or ""),
            "model": str(model or ""),
            "cost_usd": float(cost_usd) if isinstance(cost_usd, (int, float)) else None,
        }
    )
    if len(usage["calls"]) > _MAX_CALLS:
        usage["calls"] = usage["calls"][-_MAX_CALLS:]
    usage["total_input"] = int(usage.get("total_input") or 0) + inp
    usage["total_output"] = int(usage.get("total_output") or 0) + out
    usage["total_cache_read"] = int(usage.get("total_cache_read") or 0) + cache_read
    usage["total_cache_write"] = int(usage.get("total_cache_write") or 0) + cache_write
    conv.token_usage = usage
    # Global Settings ledger: gateway streams auto-log via make_provider.
    # Coding-agent CLIs (no make_provider) still need an explicit ledger write.
    try:
        from backend.agent.coding_agents.base import contributed_coding_agents
        from frontend.ui_web.provider_usage_log import log_call

        prov = str(provider or "").strip().lower()
        if prov and prov in contributed_coding_agents():
            log_call(
                provider=prov,
                model=str(model or ""),
                input_tokens=inp,
                output_tokens=out,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                cost_usd=float(cost_usd) if isinstance(cost_usd, (int, float)) else None,
                conv_id=str(getattr(conv, "id", "") or ""),
                agent=str(getattr(conv, "coding_agent", "") or prov),
                ducky_label=str(
                    getattr(conv, "ducky_name", "") or getattr(conv, "title", "") or ""
                ),
            )
    except Exception:
        pass
    return usage


def usage_from_messages(messages: list[dict[str, Any]]) -> tuple[int, int]:
    """Legacy fallback: sum usage blobs stored on assistant messages."""
    input_total = 0
    output_total = 0
    for message in messages:
        usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
        input_total += int(usage.get("input_tokens") or 0)
        output_total += int(usage.get("output_tokens") or 0)
    return input_total, output_total


def _cache_hit_rate(calls: list[dict[str, Any]]) -> float:
    if not calls:
        return 0.0
    last = calls[-1]
    inp = int(last.get("input_tokens") or 0)
    cached = int(last.get("cache_read_tokens") or 0)
    if inp <= 0:
        return 0.0
    return min(100.0, round((cached / inp) * 100.0, 1))


def _cache_hit_rate_cumulative(total_input: int, total_cache_read: int) -> float:
    """Cache hit rate across every call so far (total_cache_read / total_input).

    Complements ``_cache_hit_rate``'s last-call-only figure: a single missed
    cache turn (e.g. after a long idle gap) shouldn't make caching look broken
    when the chat has been mostly cached overall.
    """
    if total_input <= 0:
        return 0.0
    return min(100.0, round((total_cache_read / total_input) * 100.0, 1))


def token_usage_report(conv: Any) -> dict[str, Any]:
    """Return exact sent/received totals and per-call history for the UI."""
    logged = normalize_token_usage(getattr(conv, "token_usage", None))
    msg_in, msg_out = usage_from_messages(list(getattr(conv, "messages", None) or []))
    total_input = int(logged["total_input"] or 0)
    total_output = int(logged["total_output"] or 0)
    if total_input == 0 and total_output == 0 and (msg_in or msg_out):
        total_input = msg_in
        total_output = msg_out
    calls = list(logged.get("calls") or [])
    total_cache_read = int(logged.get("total_cache_read") or 0)
    total_cache_write = int(logged.get("total_cache_write") or 0)
    return {
        "total_input": total_input,
        "total_output": total_output,
        "total_tokens": total_input + total_output,
        "total_cache_read": total_cache_read,
        "total_cache_write": total_cache_write,
        "cache_hit_rate": _cache_hit_rate(calls),
        "cache_hit_rate_cumulative": _cache_hit_rate_cumulative(total_input, total_cache_read),
        "call_count": len(calls),
        "calls": calls,
    }


def estimate_context_window_tokens(
    input_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    *,
    num_turns: int = 0,
) -> int:
    """Best-effort *current* context window from usage buckets.

    Claude Code ``result.usage`` is often cumulative across steps. Summing
    ``input + cache_read + cache_write`` then double-counts (e.g. Sent 2.1M +
    Cached 2.1M → fake 4.2M "context"). Prefer last-step samples when available;
    this heuristic is the fallback.
    """
    inp = max(0, int(input_tokens or 0))
    cache_read = max(0, int(cache_read_tokens or 0))
    cache_write = max(0, int(cache_write_tokens or 0))
    total = inp + cache_read + cache_write
    if total <= 0:
        return 0
    turns = max(0, int(num_turns or 0))
    if turns > 1:
        return max(1, (total + turns - 1) // turns)
    # Aggregated multi-step runs often land with input ≈ cache_read and no
    # reliable num_turns — taking the larger bucket matches real window size.
    if inp > 0 and cache_read > 0:
        larger = max(inp, cache_read)
        smaller = min(inp, cache_read)
        if smaller * 5 >= larger * 4:  # within ~20%
            return larger + cache_write
    return total


def resolve_context_window_tokens(
    *,
    stored_context_tokens: int = 0,
    input_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    num_turns: int = 0,
) -> int:
    """Pick stored last-step window when trustworthy; else estimate from buckets."""
    stored = max(0, int(stored_context_tokens or 0))
    inp = max(0, int(input_tokens or 0))
    cache_read = max(0, int(cache_read_tokens or 0))
    cache_write = max(0, int(cache_write_tokens or 0))
    naive = inp + cache_read + cache_write
    estimated = estimate_context_window_tokens(
        inp, cache_read, cache_write, num_turns=num_turns
    )
    if stored > 0 and naive > 0 and stored < naive:
        # Adapter recorded a real last-step window smaller than cumulative sum.
        return stored
    # Repair inflated input+cache sums when the call log has cache tiers.
    if naive > 0 and (cache_read > 0 or cache_write > 0) and estimated > 0:
        if stored == 0 or stored >= naive:
            return estimated
    if stored > 0:
        return stored
    return estimated
