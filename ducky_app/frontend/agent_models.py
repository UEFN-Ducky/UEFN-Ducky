"""Model helpers — lists come from provider APIs only (see model_fetch.py)."""

from __future__ import annotations


def provider_label(provider: str) -> str:
    """Display label from Store contribution, else title-case id."""
    prov = (provider or "").strip().lower()
    if not prov:
        return ""
    try:
        from backend.uefn_plugins.host import get_contributions

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


def pick_first_available(cached: list[str], candidates: list[str], fallback: str = "") -> str:
    """Try each candidate in order; fall back to pick_model."""
    for candidate in candidates:
        model_id = (candidate or "").strip()
        if not model_id:
            continue
        if not cached or model_id in cached:
            return model_id
    return pick_model(cached, fallback)


def models_dropdown(cached: list[str], saved_model: str = "") -> list[str]:
    """Order models for a combobox — API list only, no filler."""
    if not cached:
        return []
    saved = (saved_model or "").strip()
    if saved and saved in cached:
        return [saved] + [m for m in cached if m != saved]
    return list(cached)


def default_provider_with_key(has_key_fn) -> str | None:
    from backend.agent.providers import all_providers

    for p in all_providers():
        if has_key_fn(p):
            return p
    return None

