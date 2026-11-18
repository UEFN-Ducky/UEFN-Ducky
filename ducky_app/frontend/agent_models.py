"""Model helpers — lists come from provider APIs only (see model_fetch.py)."""

from __future__ import annotations


def provider_label(provider: str) -> str:
    """Display label from Store contribution, else title-case id."""
    prov = (provider or "").strip().lower()
    if not prov:
        return ""
    try:
        from backend.uefn_plugins.plugin_host.ui_registry import get_contributions

        for row in get_contributions().get("llm_providers") or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("id") or "").strip().lower() == prov:
                label = str(row.get("label") or "").strip()
                if label:
                    return label
    except Exception:
        pass
    return prov.replace("_", " ").title()


# Back-compat mapping used by a few callers; prefer provider_label().
PROVIDER_LABELS: dict[str, str] = {}


def pick_model(cached: list[str], saved_model: str = "") -> str:
    """Pick saved model if still in API list, else first API model, else empty."""
    saved = (saved_model or "").strip()
    if saved and saved in cached:
        return saved
    return cached[0] if cached else ""

