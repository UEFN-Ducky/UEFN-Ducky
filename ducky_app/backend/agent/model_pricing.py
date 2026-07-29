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
    """Best-effort provider from the model id (legacy calls without recorded provider).

    Prefer a unique hit in the capability cache (seeded from models_cache.json /
    last gateway fetch). Never assume OpenAI for Ollama-style ``name:tag`` ids —
    that was routing local Qwen/Llama picks to api.openai.com (404).
    """
    raw = (model or "").strip()
    if not raw:
        return ""
    m = raw.lower()

    # Qualified "provider:model" (and "ollama:qwen3.6:latest" → provider=ollama).
    known = (
        "anthropic",
        "openai",
        "gemini",
        "ollama",
        "cursor",
        "kimi",
        "spacexai",
    )
    if ":" in raw:
        prefix = raw.split(":", 1)[0].strip().lower()
        if prefix in known:
            return prefix

    try:
        from backend.agent.model_fetch import _CAPABILITY_CACHE

        hits = sorted(
            {
                prov
                for (prov, mid) in _CAPABILITY_CACHE
                if mid == raw or str(mid).lower() == m
            }
        )
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            for preferred in (
                "ollama",
                "anthropic",
                "openai",
                "gemini",
                "kimi",
                "spacexai",
                "cursor",
            ):
                if preferred in hits:
                    return preferred
            return hits[0]
    except Exception:
        pass

    if "claude" in m:
        return "anthropic"
    if "gemini" in m:
        return "gemini"
    if m.startswith("grok"):
        return "spacexai"
    if m.startswith("kimi") or m.startswith("moonshot"):
        return "kimi"
    # Local Ollama tags (qwen3.6:latest, llama3.2:8b) — not OpenAI.
    if ":" in raw and not m.startswith("ft:"):
        return "ollama"
    if m.startswith(("gpt-", "o1", "o3", "o4", "chatgpt")):
        return "openai"
    return "openai"


def resolve_provider_for_model(model: str, recorded_provider: str = "") -> str:
    """Pick the gateway for a model, correcting stale mis-routes.

    Recorded provider wins when it still owns the model in cache. Otherwise
    fall back to ``infer_provider`` (fixes chats that stored ``openai`` for
    ``qwen3.6:latest`` etc.).
    """
    mid = (model or "").strip()
    recorded = (recorded_provider or "").strip().lower()
    if not mid:
        return recorded
    inferred = infer_provider(mid)
    if not recorded:
        return inferred
    if recorded == inferred:
        return recorded
    try:
        from backend.agent.model_fetch import get_model_info

        if get_model_info(recorded, mid) is not None:
            return recorded
        if inferred and get_model_info(inferred, mid) is not None:
            return inferred
    except Exception:
        pass
    # Ollama-style tags must not stay stuck on a cloud gateway.
    if inferred == "ollama" and recorded in {
        "openai",
        "anthropic",
        "gemini",
        "kimi",
        "spacexai",
        "cursor",
    }:
        return "ollama"
    return recorded or inferred


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
