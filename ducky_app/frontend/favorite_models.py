"""Strict Ducky model selection with a global default fallback.

Stored shape (a profile's favorite_models holds at most one entry):
  "{backend}:{model_id}"

backend is either a registered API / gateway provider id or a coding-agent id
from Store contributions. A model set on the profile always wins; when the
profile has none, settings.default_model (Settings → LLMs) applies. Legacy bare
ids are preserved for display but fail resolution until the user re-picks an
exact model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def coding_agent_backends() -> frozenset[str]:
    try:
        from backend.agent.coding_agents.base import contributed_coding_agents

        return frozenset(contributed_coding_agents())
    except Exception:
        return frozenset()


def api_backends() -> frozenset[str]:
    try:
        from backend.agent.providers import gateway_providers

        return frozenset(gateway_providers())
    except Exception:
        return frozenset()


def known_backends() -> frozenset[str]:
    return coding_agent_backends() | api_backends()


# Back-compat names for importers / tests — resolved at attribute access time.
def __getattr__(name: str) -> Any:
    if name == "CODING_AGENT_BACKENDS":
        return coding_agent_backends()
    if name == "API_BACKENDS":
        return api_backends()
    if name == "KNOWN_BACKENDS":
        return known_backends()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@dataclass(frozen=True)
class FavoriteSelection:
    backend: str
    model_id: str

    @property
    def qualified(self) -> str:
        return f"{self.backend}:{self.model_id}"

    @property
    def is_coding_agent(self) -> bool:
        return self.backend in coding_agent_backends()

    @property
    def coding_agent(self) -> str:
        return self.backend if self.is_coding_agent else "ducky"

    @property
    def provider(self) -> str:
        return "" if self.is_coding_agent else self.backend


@dataclass(frozen=True)
class ResolveOk:
    coding_agent: str
    model: str
    provider: str
    selection: FavoriteSelection


@dataclass(frozen=True)
class ResolveErr:
    code: str
    message: str
    requested: str = ""


ResolveResult = ResolveOk | ResolveErr


def qualify(backend: str, model_id: str) -> str:
    return f"{(backend or '').strip()}:{(model_id or '').strip()}"


def parse_selection(raw: str) -> FavoriteSelection | None:
    """Parse a qualified selection. Returns None for empty/legacy/invalid values."""
    text = (raw or "").strip()
    if not text or ":" not in text:
        return None
    backend, model_id = text.split(":", 1)
    backend = backend.strip().lower().replace("-", "_")
    model_id = model_id.strip()
    if not backend or not model_id:
        return None
    if backend not in known_backends():
        return None
    # Plugin normalize_model (e.g. Cursor default→auto). Bare "default" otherwise invalid.
    try:
        from backend.uefn_plugins.host import get_coding_agent_registration

        reg = get_coding_agent_registration(backend) or {}
        norm = reg.get("normalize_model")
        if callable(norm):
            model_id = str(norm(model_id) or model_id).strip() or model_id
    except Exception:
        pass
    if model_id.lower() == "default":
        return None
    return FavoriteSelection(backend=backend, model_id=model_id)


def is_legacy_agent_only(raw: str) -> bool:
    key = (raw or "").strip().lower().replace("-", "_")
    return key in coding_agent_backends()


def first_favorite(raw: Any) -> str:
    if not isinstance(raw, list):
        return ""
    for item in raw:
        text = str(item or "").strip()
        if text:
            return text
    return ""


def _available_agent_models(settings: Any) -> dict[str, set[str]]:
    from backend.agent.coding_agents import detect_all

    out: dict[str, set[str]] = {}
    agents = coding_agent_backends()
    try:
        payload = detect_all(settings)
    except Exception:
        return out
    for info in payload.get("agents") or []:
        if not isinstance(info, dict):
            continue
        aid = str(info.get("id") or "").strip()
        if aid not in agents:
            continue
        if not info.get("enabled", True) or not info.get("available", False):
            continue
        models = {
            str(m.get("id") or "").strip()
            for m in (info.get("models") or [])
            if isinstance(m, dict) and str(m.get("id") or "").strip()
        }
        out[aid] = models
    return out


def _available_api_models() -> dict[str, set[str]]:
    from backend.agent.model_fetch import fetch_models
    from backend.agent.providers import all_providers
    from backend.agent.secrets import get_key, has_key

    out: dict[str, set[str]] = {}
    for provider in all_providers():
        if not has_key(provider):
            continue
        try:
            ids = {
                (item.id if hasattr(item, "id") else str(item)).strip()
                for item in fetch_models(provider, get_key(provider))
            }
            ids.discard("")
            if ids:
                out[provider] = ids
        except Exception:
            continue
    return out


def default_model_selection(settings: Any) -> str:
    """The qualified global default from Settings → LLMs (may be empty)."""
    return str(getattr(settings, "default_model", "") or "").strip()


def resolve_model_strict(favorite_models: Any, settings: Any) -> ResolveResult:
    """Resolve the model for a Ducky/chat.

    The profile's own model (favorite_models[0]) always wins; when empty, the
    global settings.default_model applies. A missing/unavailable selection
    errors — it is never silently replaced by another model.
    """
    requested = first_favorite(favorite_models)
    where = "in the Ducky profile"
    if not requested:
        requested = default_model_selection(settings)
        where = "in Settings → LLMs → Default Model"
    if not requested:
        return ResolveErr(
            code="model_required",
            message=(
                "No model selected. Pick a model on this Ducky, or set a "
                "Default Model in Settings → LLMs so every Ducky without one uses it."
            ),
        )

    if is_legacy_agent_only(requested):
        return ResolveErr(
            code="model_needs_repick",
            message=(
                f"Saved choice {requested!r} is an agent name, not an exact model. "
                f"Re-pick a concrete model (e.g. Cursor → composer-2.5) {where}."
            ),
            requested=requested,
        )

    selection = parse_selection(requested)
    if selection is None:
        # Legacy bare API model id — only accept if uniquely present in one provider catalog.
        bare = requested.strip()
        api_models = _available_api_models()
        matches = [
            (provider, bare)
            for provider, ids in api_models.items()
            if bare in ids
        ]
        if len(matches) == 1:
            provider, model_id = matches[0]
            selection = FavoriteSelection(backend=provider, model_id=model_id)
        else:
            return ResolveErr(
                code="model_unavailable",
                message=(
                    f"Saved model {bare!r} is missing or ambiguous. "
                    f"Re-pick an exact model {where}."
                ),
                requested=requested,
            )

    if selection.is_coding_agent:
        agent_models = _available_agent_models(settings)
        models = agent_models.get(selection.backend)
        if models is None:
            return ResolveErr(
                code="model_unavailable",
                message=(
                    f"Coding agent {selection.backend!r} is unavailable. "
                    f"Enable it in Settings → LLMs → Coding Agents, or re-pick the model {where}."
                ),
                requested=selection.qualified,
            )
        if selection.model_id not in models:
            return ResolveErr(
                code="model_unavailable",
                message=(
                    f"Model {selection.model_id!r} is not advertised by {selection.backend}. "
                    f"Re-pick a model from the live list {where}."
                ),
                requested=selection.qualified,
            )
        return ResolveOk(
            coding_agent=selection.coding_agent,
            model=selection.model_id,
            provider="",
            selection=selection,
        )

    api_models = _available_api_models()
    provider_ids = api_models.get(selection.backend)
    if not provider_ids or selection.model_id not in provider_ids:
        return ResolveErr(
            code="model_unavailable",
            message=(
                f"Model {selection.model_id!r} is not available from {selection.backend}. "
                f"Re-pick a model from the live list {where}."
            ),
            requested=selection.qualified,
        )
    return ResolveOk(
        coding_agent="ducky",
        model=selection.model_id,
        provider=selection.provider,
        selection=selection,
    )
