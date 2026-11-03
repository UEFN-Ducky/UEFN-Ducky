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

from dataclasses import dataclass, replace
from typing import Any


def coding_agent_backends() -> frozenset[str]:
    try:
        from backend.agent.coding_agents.base import contributed_coding_agents

        return frozenset(contributed_coding_agents())
    except Exception:
        return frozenset()


def _contributed_api_backends() -> set[str]:
    """plugin.json gateway ids — known before register() finishes a factory."""
    try:
        from backend.uefn_plugins.host import get_contributions, get_ui_contributions
    except Exception:
        return set()
    out: set[str] = set()
    for src in (get_ui_contributions, get_contributions):
        try:
            rows = src().get("llm_providers") or []
        except Exception:
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("id") or "").strip().lower()
            if pid:
                out.add(pid)
    return out


def api_backends() -> frozenset[str]:
    ids = _contributed_api_backends()
    try:
        from backend.agent.providers import gateway_providers

        ids.update(gateway_providers())
    except Exception:
        pass
    return frozenset(ids)


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
    # The failing choice is Settings → LLMs → Default Model, not the profile's own pick.
    from_default: bool = False


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


# Cold-cache probe limit for the one agent being resolved. Plugin detect() runs
# `<cli> --version`; on Windows a timed-out .cmd wrapper leaves node holding the
# pipes and subprocess.run never returns — so never wait on it unbounded.
_COLD_PROBE_TIMEOUT_S = 45.0


def _probe_agent(settings: Any, agent_id: str) -> dict[str, Any] | None:
    import threading

    from backend.agent import coding_agents

    adapter = coding_agents.get_adapter(agent_id)
    if adapter is None:
        return None
    box: dict[str, Any] = {}

    def _run() -> None:
        try:
            box["info"] = adapter.detect(settings).to_dict()
        except Exception:
            pass

    t = threading.Thread(target=_run, daemon=True, name=f"detect-{agent_id}")
    t.start()
    t.join(_COLD_PROBE_TIMEOUT_S)
    return box.get("info")


def _available_agent_models(settings: Any, backend: str = "") -> dict[str, set[str]]:
    from backend.agent import coding_agents

    out: dict[str, set[str]] = {}
    agents = coding_agent_backends()
    try:
        # The cached probe (TTL + async refresh). Passing ``settings`` here used to
        # force a fresh synchronous probe of every CLI (claude, gemini, codex,
        # cursor…) on each create-ducky — 3-4 s of "Creating…". Only before the
        # first probe has ever landed (payload still "checking", e.g. a fresh MCP
        # bridge process) do we probe directly — just ``backend``, bounded, so a
        # hung Gemini CLI cannot block spawning a Claude Code ducky.
        payload = coding_agents.detect_all()
        # The refresh publishes each agent as its probe lands, without "checking": until
        # ``backend``'s own probe lands its row is still the "Checking…" stub (available
        # False), and a Claude Code lead's seats were refused as "unavailable".
        pending = backend and any(
            isinstance(a, dict) and a.get("id") == backend and a.get("status") == "Checking…"
            for a in payload.get("agents") or []
        )
        if payload.get("checking") or pending:
            if backend:
                info = _probe_agent(settings, backend)
                payload = {"agents": [info] if info else []}
            else:
                payload = coding_agents.detect_all(settings)
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
    """In-memory catalog only — never fetch_models on the pywebview thread.

    Create-ducky / resolve used to hit every provider API here and froze the
    panel until those calls timed out (~120s).
    """
    try:
        from frontend.ui_web.panel_api import cached_api_model_ids, kick_model_refresh_if_stale
    except Exception:
        return {}
    out = cached_api_model_ids()
    if not out:
        kick_model_refresh_if_stale()
    return out


def default_model_selection(settings: Any) -> str:
    """The qualified global default from Settings → LLMs (may be empty)."""
    return str(getattr(settings, "default_model", "") or "").strip()


def allow_create_without_model(result: ResolveResult) -> ResolveResult:
    """Create paths may open with no model, also when the Default Model is no longer offered.
    A bad explicit pick still errors.

    The chat's first message then takes the Default Model or a gateway's fallback model, else waits for the
    person to pick a model (agent_modes.run_message).
    """
    if isinstance(result, ResolveErr) and (result.code == "model_required" or result.from_default):
        return ResolveOk(
            coding_agent="ducky",
            model="",
            provider="",
            selection=FavoriteSelection(backend="", model_id=""),
        )
    return result


def billing_kind(coding_agent: str, provider: str, model: str = "") -> str:
    """How a chat's turns are paid: "subscription" (a coding agent's own login),
    "local" (a model on this PC), "account" (the UEFN Ducky account) or "api_key"
    (billed per token to a saved key)."""
    agent = (coding_agent or "").strip().lower()
    if agent and agent != "ducky":
        return "subscription"
    prov = (provider or "").strip().lower()
    if not prov:
        return ""
    try:
        from backend.uefn_plugins.host import provider_option

        billing = str(provider_option(prov, "billing") or "")
    except Exception:
        billing = ""
    if billing:
        return billing
    try:
        from backend.agent.model_fetch import get_model_info

        info = get_model_info(prov, model)
        if info is not None and info.is_local:
            return "local"
    except Exception:
        pass
    return "local" if prov in ("ollama", "lmstudio", "llamacpp") else "api_key"


def refuse_paid_api_for_agent(result: ResolveResult, settings: Any) -> ResolveResult:
    """An agent seating or spawning a chat never puts it on a pay-per-token API key the
    person has not opened to agents (Settings → LLMs → the provider's key)."""
    if not isinstance(result, ResolveOk) or result.coding_agent != "ducky" or not result.model:
        return result
    if billing_kind("ducky", result.provider, result.model) != "api_key":
        return result
    allowed = {str(p).strip().lower() for p in (getattr(settings, "agent_api_keys", None) or [])}
    if result.provider.strip().lower() in allowed:
        return result
    offered = sorted(aid for aid, ids in _available_agent_models(settings).items() if result.model in ids)
    hint = (f" Use {offered[0]}:{result.model}, which runs on that subscription." if offered
            else " Pick a subscription coding agent's model (e.g. codex:…) or a local model.")
    return ResolveErr(
        code="api_key_blocked",
        message=(f"{result.selection.qualified} bills the person's {result.provider} API key per token, "
                 f"and agents may not start chats on it.{hint} Only the person can allow agents to use "
                 "this key, in Settings → LLMs."),
        requested=result.selection.qualified,
    )


def resolve_model_strict(favorite_models: Any, settings: Any) -> ResolveResult:
    """Resolve the model for a Ducky/chat.

    The profile's own model (favorite_models[0]) always wins; when empty, the
    global settings.default_model applies. A missing/unavailable selection
    errors — it is never silently replaced by another model.
    """
    result = _resolve_model_strict(favorite_models, settings)
    if isinstance(result, ResolveErr) and not first_favorite(favorite_models):
        return replace(result, from_default=True)
    return result


def _resolve_model_strict(favorite_models: Any, settings: Any) -> ResolveResult:
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
        bare = requested.strip()
        # A bare id a logged-in coding agent offers runs there, on its subscription: an
        # agent that wrote "gpt-6.1-sol" got OpenAI's per-token API instead of Codex.
        subscribed = sorted(aid for aid, ids in _available_agent_models(settings).items() if bare in ids)
        if subscribed:
            selection = FavoriteSelection(backend=subscribed[0], model_id=bare)
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
        agent_models = _available_agent_models(settings, backend=selection.backend)
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
    if provider_ids is not None and selection.model_id not in provider_ids:
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


def fallback_selection() -> ResolveOk | None:
    """The model a plugin's gateway offers for a chat with no usable model (its
    ``fallback_model`` option), while visible."""
    try:
        from backend.uefn_plugins.host import provider_option, provider_visible, registered_providers
    except Exception:
        return None
    backends = api_backends()
    for provider in registered_providers():
        wanted = str(provider_option(provider, "fallback_model") or "").strip()
        if not wanted or provider not in backends or not provider_visible(provider):
            continue
        ids = _available_api_models().get(provider) or set()
        model = wanted if not ids or wanted in ids else sorted(ids)[0]
        return ResolveOk(
            coding_agent="ducky",
            model=model,
            provider=provider,
            selection=FavoriteSelection(backend=provider, model_id=model),
        )
    return None


def _first_api_model() -> ResolveOk | None:
    catalog = _available_api_models()
    for provider in sorted(catalog):
        ids = sorted(catalog.get(provider) or [])
        if ids:
            return ResolveOk(coding_agent="ducky", model=ids[0], provider=provider,
                             selection=FavoriteSelection(backend=provider, model_id=ids[0]))
    return None


def _same_agent_model(result: ResolveErr, settings: Any) -> ResolveOk | None:
    """A Default Model its coding agent no longer offers: stay on that agent, whose own login
    pays, with its Auto model (else the first it offers). Oct 21 2026: Codex stopped listing
    gpt-6-astra and every new chat quietly moved to another gateway's fallback model."""
    selection = parse_selection(result.requested or "")
    if selection is None or not selection.is_coding_agent:
        return None
    models = _available_agent_models(settings, backend=selection.backend).get(selection.backend)
    if not models:
        return None  # the agent itself is gone: a gateway's fallback model it is
    model = "auto" if "auto" in models else sorted(models)[0]
    return ResolveOk(
        coding_agent=selection.coding_agent,
        model=model,
        provider="",
        selection=FavoriteSelection(backend=selection.backend, model_id=model),
    )


def chat_model_choice(settings: Any, keep_agent: bool = False) -> tuple[ResolveOk | None, str]:
    """The model a chat with none takes: the Default Model, else a gateway's fallback model.

    Returns (choice, error). A chat that takes a fallback model just shows that model: the
    composer says nothing about the Default Model. error says why there is no model at all,
    and the person then picks one in the composer. keep_agent: the chat stays on Ducky's own
    agent, so a coding-agent Default Model does not apply and the first API model is the
    last resort.
    """
    result = resolve_model_strict(None, settings)
    if isinstance(result, ResolveOk) and result.model and not (keep_agent and result.coding_agent != "ducky"):
        return result, ""
    same_agent = None if keep_agent or not isinstance(result, ResolveErr) else _same_agent_model(result, settings)
    if same_agent is not None:
        return same_agent, ""
    fallback = fallback_selection() or (_first_api_model() if keep_agent else None)
    if fallback is not None:
        return fallback, ""
    if isinstance(result, ResolveOk):
        why = "This ducky has no model"
    elif result.code == "model_required":
        why = "No Default Model is set"
    else:
        why = f"Your Default Model {result.requested or default_model_selection(settings)} isn't available"
    return None, f"{why}. Pick a model below and this ducky uses it."
