"""Fetch model lists and metadata from provider APIs — no hardcoded catalogs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_CACHE_MAX = 512

_CAPABILITY_CACHE: dict[tuple[str, str], ModelInfo] = {}
# USD per 1M tokens: (input, output, cached_input, cache_write) — used by plugins
_PricingRow = tuple[float, float, float | None, float | None]


def _cache_put(cache: dict, key: Any, value: Any) -> None:
    cache[key] = value
    if len(cache) > _CACHE_MAX:
        # Drop arbitrary oldest-inserted keys (dict preserves insertion on 3.7+).
        for stale in list(cache.keys())[: len(cache) - _CACHE_MAX]:
            cache.pop(stale, None)


@dataclass(frozen=True)
class ModelInfo:
    id: str
    display_name: str | None = None
    supports_vision: bool = False
    supports_tools: bool = False
    supports_web_search: bool = False
    context_limit: int | None = None
    price_in: float | None = None
    price_out: float | None = None
    price_cached_in: float | None = None
    price_cache_write: float | None = None
    is_local: bool = False


def get_model_info(provider: str, model_id: str) -> ModelInfo | None:
    prov = (provider or "").strip().lower()
    mid = (model_id or "").strip()
    if not prov or not mid:
        return None
    return _CAPABILITY_CACHE.get((prov, mid))


def clear_model_cache(provider: str | None = None) -> None:
    """Drop cached model metadata (e.g. after API key change)."""
    if provider:
        prov = provider.strip().lower()
        for key in [k for k in _CAPABILITY_CACHE if k[0] == prov]:
            del _CAPABILITY_CACHE[key]
    else:
        _CAPABILITY_CACHE.clear()
    # Best-effort: ask registered gateways to drop their private caches.
    try:
        from backend.uefn_plugins.host import (
            get_llm_provider_registration,
            registered_llm_provider_ids,
        )

        ids = (provider.strip().lower(),) if provider else registered_llm_provider_ids()
        for pid in ids:
            reg = get_llm_provider_registration(pid) or {}
            clear_fn = reg.get("clear_model_cache")
            if callable(clear_fn):
                clear_fn()
    except Exception:
        pass


def _cache_provider_models(provider: str, models: list[ModelInfo]) -> None:
    prov = (provider or "").strip().lower()
    for m in models:
        _cache_put(_CAPABILITY_CACHE, (prov, m.id), m)


def fetch_models(provider: str, api_key: str, *, verify_openai: bool = False) -> list[ModelInfo]:
    """Return model metadata from the provider API for the given key."""
    name = (provider or "").strip().lower()
    key = (api_key or "").strip()
    models: list[ModelInfo] = []
    try:
        from backend.uefn_plugins.host import get_llm_provider_registration

        reg = get_llm_provider_registration(name) or {}
        norm = reg.get("normalize_secret")
        if callable(norm):
            try:
                key = str(norm(key) or "").strip()
            except Exception:
                pass
        if not key and not reg.get("key_optional"):
            return []
        fetch_fn = reg.get("fetch_models")
        if callable(fetch_fn):
            try:
                models = list(fetch_fn(key, verify=verify_openai))
            except TypeError:
                models = list(fetch_fn(key))
    except Exception:
        models = []
    _cache_provider_models(name, models)
    return models


def _int_from_record(record: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        val = record.get(key)
        if isinstance(val, (int, float)) and val > 0:
            return int(val)
    return None


def _float_from_record(record: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        val = record.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    return None


def _parse_price_cell(val: Any) -> float | None:
    if isinstance(val, (int, float)):
        return float(val)
    if not isinstance(val, str):
        return None
    s = val.strip().strip('"').strip("'")
    if not s or s in {"-", "null", '""', "''"}:
        return None
    m = re.search(r"([\d.]+)", s)
    return float(m.group(1)) if m else None


def _per_million_from_token_rate(val: float) -> float:
    """Convert USD per token to USD per 1M tokens when value looks like a token rate."""
    if 0 < val < 0.01:
        return val * 1_000_000
    return val


def _merge_prices(
    primary: tuple[float | None, float | None, float | None, float | None],
    fallback: _PricingRow | None,
) -> _PricingRow | tuple[float | None, float | None, float | None, float | None]:
    if fallback is None:
        return primary
    pin, pout, pcached, pwrite = primary
    fin, fout, fcached, fwrite = fallback
    return (
        pin if pin is not None else fin,
        pout if pout is not None else fout,
        pcached if pcached is not None else fcached,
        pwrite if pwrite is not None else fwrite,
    )

# Vendor fetch/pricing lives in Store gateway plugins (openai/anthropic/google/ollama).
