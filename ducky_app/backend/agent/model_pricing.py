"""Per-model API pricing for the context panel cost display.

Prices are USD per 1M tokens: (input, cached_input, output, cache_write).
All values come from the provider model API cache — no hardcoded price tables.
"""

from __future__ import annotations

from typing import Any

from backend.agent.model_fetch import get_model_info


def price_for_model(model: str, provider: str = "") -> tuple[float, float, float, float] | None:
    prov = (provider or "").strip().lower()
    info = get_model_info(prov, model)
    if info is None or info.price_in is None or info.price_out is None:
        return None
    cached = info.price_cached_in if info.price_cached_in is not None else info.price_in
    cache_write = info.price_cache_write if info.price_cache_write is not None else 0.0
    return (info.price_in, cached, info.price_out, cache_write)


def infer_provider(model: str) -> str:
    """Best-effort provider from the model id (legacy calls without recorded provider)."""
    m = (model or "").strip().lower()
    if "claude" in m:
        return "anthropic"
    if "gemini" in m:
        return "gemini"
    if m:
        return "openai"
    return ""


def estimate_cost_usd(
    provider: str,
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    """Estimated USD cost of one or more API calls; None when pricing is unknown."""
    prices = price_for_model(model, provider)
    if prices is None:
        return None
    in_p, cached_p, out_p, write_p = prices
    inp = max(0, int(input_tokens or 0))
    out = max(0, int(output_tokens or 0))
    cread = max(0, int(cache_read_tokens or 0))
    cwrite = max(0, int(cache_write_tokens or 0))
    # "inclusive_input" (Anthropic): bill full input + cache read/write separately.
    # Default: input tokens already exclude cache hits (OpenAI-style).
    cost_mode = ""
    try:
        from backend.uefn_plugins.host import get_llm_provider_registration

        cost_mode = str(
            (get_llm_provider_registration(provider) or {}).get("cost_mode") or ""
        ).strip().lower()
    except Exception:
        cost_mode = ""
    if cost_mode == "inclusive_input":
        cost = inp * in_p + cread * cached_p + cwrite * write_p + out * out_p
    else:
        uncached = max(0, inp - cread)
        cost = uncached * in_p + cread * cached_p + out * out_p
    return cost / 1_000_000.0


def call_cost_usd(
    call: dict[str, Any],
    *,
    fallback_provider: str = "",
    fallback_model: str = "",
) -> float | None:
    """Cost of one recorded API call, preferring the provider/model logged with it."""
    # An authoritative cost reported by the backend (Claude Code) wins over any
    # pricing-table estimate.
    reported = call.get("cost_usd")
    if isinstance(reported, (int, float)):
        return float(reported)
    model = str(call.get("model") or "").strip() or fallback_model
    provider = str(call.get("provider") or "").strip() or infer_provider(model) or fallback_provider
    return estimate_cost_usd(
        provider,
        model,
        input_tokens=int(call.get("input_tokens") or 0),
        output_tokens=int(call.get("output_tokens") or 0),
        cache_read_tokens=int(call.get("cache_read_tokens") or 0),
        cache_write_tokens=int(call.get("cache_write_tokens") or 0),
    )


def usage_cost_report(provider: str, model: str, calls: list[dict[str, Any]]) -> float | None:
    """Sum per-call cost estimates; None when no call has known pricing."""
    costs = [call_cost_usd(c, fallback_provider=provider, fallback_model=model) for c in calls]
    known = [c for c in costs if c is not None]
    if not known:
        return None
    return sum(known)
