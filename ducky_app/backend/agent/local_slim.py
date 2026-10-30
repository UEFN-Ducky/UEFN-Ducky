"""Detect gateways that need a shorter frozen prompt (slim indexes + volatile tail)."""

from __future__ import annotations


def provider_wants_local_slim(provider: str) -> bool:
    """True for Ollama, ``cache_mode=local`` gateways, and gateways that register
    ``slim_prompt=True`` (a small window, e.g. Ducky AI's 128k: the full prompt was
    ~166k tokens a turn, over its own context limit before any work)."""
    name = (provider or "").strip().lower()
    if not name:
        return False
    if name == "ollama":
        return True
    try:
        from backend.uefn_plugins.host import get_llm_provider_registration

        reg = get_llm_provider_registration(name) or {}
        return reg.get("slim_prompt") is True or str(reg.get("cache_mode") or "").strip().lower() == "local"
    except Exception:
        return False
