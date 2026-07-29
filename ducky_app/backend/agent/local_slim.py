"""Detect local LLM gateways that need a shorter frozen prompt + KV hygiene."""

from __future__ import annotations


def provider_wants_local_slim(provider: str) -> bool:
    """True for Ollama / cache_mode=local gateways — slim indexes + volatile tail."""
    name = (provider or "").strip().lower()
    if not name:
        return False
    if name == "ollama":
        return True
    try:
        from backend.uefn_plugins.host import get_llm_provider_registration

        reg = get_llm_provider_registration(name) or {}
        return str(reg.get("cache_mode") or "").strip().lower() == "local"
    except Exception:
        return False
