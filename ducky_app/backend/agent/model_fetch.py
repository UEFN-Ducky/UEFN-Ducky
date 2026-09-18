"""Fetch model lists and metadata from provider APIs — no hardcoded catalogs."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
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
    # None = unknown (UI falls back to provider flag / name heuristic).
    supports_thinking_effort: bool | None = None
    # Gateway-owned effort stops. Host never invents levels besides Off=0.
    thinking_menu: dict[str, Any] | None = None


_MODEL_INFO_FIELDS = {f.name for f in fields(ModelInfo)}


def model_info_from_row(row: dict[str, Any]) -> ModelInfo:
    """Build ModelInfo from a cache/API dict, ignoring unknown keys."""
    return ModelInfo(**{k: v for k, v in row.items() if k in _MODEL_INFO_FIELDS})


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


def normalize_usage_windows(raw: Any) -> dict[str, Any]:
    """Clamp plugin `fetch_usage` output. Missing/junk → empty windows."""
    rows = raw.get("windows") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return {"windows": []}
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            limit = float(row.get("limit") or 0)
            used = float(row.get("used") or 0)
        except (TypeError, ValueError):
            continue
        if limit <= 0:
            continue
        used = max(0.0, min(used, limit))
        wid = str(row.get("id") or "").strip() or f"w{len(out)}"
        label = str(row.get("label") or wid).strip() or wid
        unit = str(row.get("unit") or "").strip()
        item: dict[str, Any] = {"id": wid, "label": label, "used": used, "limit": limit}
        if unit:
            item["unit"] = unit
        out.append(item)
    return {"windows": out}


def _usage_registration(provider: str) -> tuple[str, dict[str, Any]]:
    """LLM provider row, or coding-agent `token_provider` fallback."""
    from backend.uefn_plugins.host import (
        get_coding_agent_registration,
        get_llm_provider_registration,
    )

    name = (provider or "").strip().lower()
    reg = get_llm_provider_registration(name) or {}
    if callable(reg.get("fetch_usage")):
        return name, reg
    agent = get_coding_agent_registration(name) or {}
    token = str(agent.get("token_provider") or "").strip().lower()
    if token:
        return token, get_llm_provider_registration(token) or {}
    return name, reg


def fetch_usage(provider: str, api_key: str, *, model: str = "") -> dict[str, Any]:
    """Live quota windows from the gateway plugin. Never cached."""
    name, reg = _usage_registration(provider)
    key = (api_key or "").strip()
    try:
        norm = reg.get("normalize_secret")
        if callable(norm):
            try:
                key = str(norm(key) or "").strip()
            except Exception:
                pass
        if not key and name != (provider or "").strip().lower():
            from backend.agent.secrets import get_key

            key = str(get_key(name) or "").strip()
        if not key and not reg.get("key_optional"):
            return {"windows": []}
        fn = reg.get("fetch_usage")
        if not callable(fn):
            return {"windows": []}
        try:
            raw = fn(key, model=str(model or ""))
        except TypeError:
            raw = fn(key)
        return normalize_usage_windows(raw)
    except Exception:
        return {"windows": []}


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
