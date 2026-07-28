"""Generic one-shot LLM complete for desktop plugins (model resolve + stream).

Used only by `plugin_host_api.llm_complete` — not feature-specific.
Coding-agent / provider specials come from Store plugin registrations.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable

from backend.agent.providers import make_provider
from backend.agent.providers.base import ProviderMessage, StreamEventKind
from backend.agent.secrets import get_key
from frontend.favorite_models import ResolveOk, resolve_model_strict
from frontend.settings import PanelSettings

log = logging.getLogger("uefn.plugin_llm")

# Optional inject for tests: (provider_name, model, system, user) -> response text
_CompleteFn = Callable[..., str]
_complete_override: _CompleteFn | None = None


def _api_backends() -> frozenset[str]:
    try:
        from backend.agent.providers import gateway_providers

        return frozenset(gateway_providers())
    except Exception:
        return frozenset()


def _coding_agent_backends() -> frozenset[str]:
    try:
        from backend.agent.coding_agents.base import contributed_coding_agents

        return frozenset(contributed_coding_agents())
    except Exception:
        return frozenset()


def _route_coding_agent(backend: str, model_id: str) -> tuple[str, str]:
    """Map a coding-agent pick to a completion backend (api provider or agent id)."""
    from backend.agent.coding_agents.base import contributed_coding_agents
    from backend.uefn_plugins.host import get_coding_agent_registration

    mid = (model_id or "").strip()
    backend = (backend or "").strip().lower().replace("-", "_")

    if backend not in contributed_coding_agents():
        raise ValueError(
            f"{backend!r} requires its Store gateway — Settings → Store → Gateways"
        )
    reg = get_coding_agent_registration(backend) or {}
    resolve_fb = reg.get("resolve_api_fallback")
    if callable(resolve_fb):
        try:
            fb = resolve_fb(mid)
        except Exception:
            fb = None
        if isinstance(fb, (tuple, list)) and len(fb) >= 2:
            return str(fb[0]).strip().lower(), str(fb[1]).strip() or mid
    return backend, mid or "auto"


def _resolve_api_model(*, model: str = "", provider: str = "") -> tuple[str, str]:
    """Return (backend, model) for plugin LLM complete — API providers OR coding agents."""
    from frontend.favorite_models import parse_selection

    prov = (provider or "").strip().lower()
    mid = (model or "").strip()
    api_backends = _api_backends()
    coding_backends = _coding_agent_backends()

    if mid and ":" in mid and not prov:
        sel = parse_selection(mid)
        if sel and sel.provider in api_backends and sel.model_id:
            return sel.provider, sel.model_id
        if sel and sel.is_coding_agent:
            return _route_coding_agent(sel.backend, sel.model_id)

    if prov in api_backends and mid:
        if ":" in mid:
            sel = parse_selection(mid if ":" in mid else f"{prov}:{mid}")
            if sel and sel.is_coding_agent:
                return _route_coding_agent(sel.backend, sel.model_id)
            if sel and sel.provider in api_backends:
                return sel.provider, sel.model_id
        bare = mid.split(":", 1)[-1].strip() or mid
        return prov, bare

    if prov in coding_backends and mid:
        return _route_coding_agent(prov, mid)

    settings = PanelSettings.load()
    result = resolve_model_strict(None, settings)
    if isinstance(result, ResolveOk):
        if result.coding_agent and result.coding_agent != "ducky":
            return _route_coding_agent(result.coding_agent, result.model)
        if result.provider in api_backends and result.model:
            return result.provider, result.model

    fallback_prov = (settings.agent_provider or "").strip().lower()
    fallback_model = (settings.agent_model or "").strip()
    if ":" in fallback_model:
        sel = parse_selection(fallback_model)
        if sel and sel.is_coding_agent:
            return _route_coding_agent(sel.backend, sel.model_id)
        if sel and sel.provider in api_backends and sel.model_id:
            return sel.provider, sel.model_id
    if fallback_prov in api_backends and fallback_model:
        return fallback_prov, fallback_model
    if fallback_prov in coding_backends and fallback_model:
        return _route_coding_agent(fallback_prov, fallback_model)

    raise ValueError(
        "Pick a model in the plugin settings, or set a Default Model in Settings → LLMs."
    )


async def _complete_text(
    *,
    provider_name: str,
    model: str,
    system: str,
    user: str,
    usage_agent: str = "plugin",
) -> str:
    if _complete_override is not None:
        return _complete_override(provider_name, model, system, user)

    from backend.agent.coding_agents.base import contributed_coding_agents
    from backend.agent.providers import gateway_providers
    from backend.uefn_plugins.host import get_coding_agent_registration
    from frontend.ui_web.provider_usage_log import (
        bind_usage_context,
        log_gateway_usage,
        reset_usage_context,
    )

    pname = (provider_name or "").strip().lower().replace("-", "_")
    agent = (usage_agent or "plugin").strip() or "plugin"
    ctx = bind_usage_context(agent=agent, ducky_label=agent.replace("_", " ").title())
    try:
        async def _one_shot_complete(one_shot: Any) -> str:
            text = await asyncio.to_thread(one_shot, model=model, system=system, user=user)
            # CLI one-shots skip make_provider — log explicitly.
            log_gateway_usage(
                provider=pname,
                model=model,
                estimate_chars=len(system) + len(user) + len(text or ""),
                agent=agent,
                ducky_label=agent.replace("_", " ").title(),
            )
            return text

        if pname in contributed_coding_agents():
            reg = get_coding_agent_registration(pname) or {}
            resolve_fb = reg.get("resolve_api_fallback")
            if callable(resolve_fb):
                try:
                    fb = resolve_fb(model)
                except Exception:
                    fb = None
                if isinstance(fb, (tuple, list)) and len(fb) >= 2:
                    pname = str(fb[0]).strip().lower()
                    model = str(fb[1]).strip() or model
                else:
                    one_shot = reg.get("complete_one_shot")
                    if callable(one_shot):
                        return await _one_shot_complete(one_shot)
                    raise ValueError(f"{pname} gateway cannot complete without its API key or CLI.")
            else:
                one_shot = reg.get("complete_one_shot")
                if callable(one_shot):
                    return await _one_shot_complete(one_shot)
                raise ValueError(f"{pname} gateway cannot complete without a registered one-shot handler.")

        if pname not in gateway_providers():
            raise ValueError(
                f"{pname} gateway is not installed — Settings → Store → Gateways"
            )

        api_key = get_key(pname)
        if not api_key and pname != "ollama":
            raise ValueError(f"No API key for {pname}")
        provider = make_provider(pname, api_key or "", model)
        cancel = threading.Event()
        text = ""
        async for event in provider.stream_turn(
            system=system,
            messages=[ProviderMessage(role="user", content=user)],
            tools=[],
            cancel_event=cancel,
        ):
            if event.kind == StreamEventKind.TEXT_DELTA:
                text += event.text
            elif event.kind == StreamEventKind.ERROR:
                raise ValueError(event.error or "LLM complete failed")
        if not text.strip():
            raise ValueError("Model returned empty response")
        return text
    finally:
        reset_usage_context(ctx)


def set_complete_override(fn: _CompleteFn | None) -> None:
    """Test hook: replace the LLM call."""
    global _complete_override
    _complete_override = fn
