"""Which backends can run one-shot / batch LLM complete (not interactive CLIs)."""

from __future__ import annotations


def supports_batch_complete(provider_name: str) -> bool:
    """True for registered API gateways. Coding agents opt in via registration."""
    name = (provider_name or "").strip().lower().replace("-", "_")
    if not name or name == "ducky":
        return True
    try:
        from backend.agent.providers import gateway_providers

        if name in gateway_providers():
            return True
    except Exception:
        pass
    try:
        from backend.uefn_plugins.host import get_coding_agent_registration

        reg = get_coding_agent_registration(name) or {}
        # Explicit opt-in only — complete_one_shot alone is not enough (CLI agents).
        if reg.get("supports_batch_complete"):
            return True
    except Exception:
        pass
    return False
