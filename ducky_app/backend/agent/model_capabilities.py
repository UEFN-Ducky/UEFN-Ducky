"""Model capability flags — read from provider API cache only."""

from __future__ import annotations

from backend.agent.model_fetch import get_model_info


def supports_vision(provider: str, model_id: str) -> bool:
    info = get_model_info(provider, model_id)
    return bool(info and info.supports_vision)


def supports_tools(provider: str, model_id: str) -> bool:
    info = get_model_info(provider, model_id)
    return bool(info and info.supports_tools)


def supports_web_search(provider: str, model_id: str) -> bool:
    info = get_model_info(provider, model_id)
    return bool(info and info.supports_web_search)


def model_in_cache(provider: str, model_id: str) -> bool:
    return get_model_info(provider, model_id) is not None
