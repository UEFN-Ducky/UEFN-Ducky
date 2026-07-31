"""LLM providers for embedded agent.

Gateways come from Store plugins via ``llm.providers`` + ``api.register_llm_provider``.
Host keeps only the protocol and registry lookup.

Every provider from ``make_provider`` is wrapped so ``stream_turn`` / ``test_connection``
always append to Settings → LLM usage — no caller can forget.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from backend.agent.providers.base import LLMProvider, StreamEvent, StreamEventKind

__all__ = [
    "LLMProvider",
    "make_provider",
    "all_providers",
    "builtin_providers",
    "gateway_providers",
]

# No always-on cloud providers — install from Settings → Store → Gateways.
_BUILTIN_PROVIDERS: tuple[str, ...] = ()


def builtin_providers() -> tuple[str, ...]:
    return _BUILTIN_PROVIDERS


def gateway_providers() -> tuple[str, ...]:
    """Enabled ``llm.providers`` ids that have a registered factory."""
    try:
        from backend.uefn_plugins.host import (
            ensure_plugins_loaded_async,
            get_contributions,
            get_llm_provider_registration,
            plugins_ready,
        )

        # Never block Store enable/disable on sync register repair (unity-mcp etc.).
        if not plugins_ready():
            ensure_plugins_loaded_async()
        out: list[str] = []
        seen: set[str] = set()
        for row in get_contributions().get("llm_providers") or []:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("id") or "").strip().lower()
            if not pid or pid in seen:
                continue
            if get_llm_provider_registration(pid) is None:
                continue
            seen.add(pid)
            out.append(pid)
        return tuple(out)
    except Exception:
        return ()


def all_providers() -> tuple[str, ...]:
    return _BUILTIN_PROVIDERS + gateway_providers()


class _UsageTrackingProvider:
    """Wrap any gateway provider so every key use hits the usage ledger."""

    def __init__(self, inner: Any, *, provider: str, model: str) -> None:
        self._inner = inner
        self._provider = provider
        self._model = model

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _log(self, *, usage: dict[str, Any] | None = None, estimate_chars: int = 0, agent: str = "") -> None:
        try:
            from frontend.ui_web.provider_usage_log import log_gateway_usage

            log_gateway_usage(
                provider=self._provider,
                model=self._model,
                usage=usage,
                estimate_chars=estimate_chars,
                agent=agent,
            )
        except Exception:
            pass

    async def stream_turn(self, **kwargs: Any) -> AsyncIterator[StreamEvent]:
        usage: dict[str, Any] = {}
        text_chars = 0
        async for event in self._inner.stream_turn(**kwargs):
            if getattr(event, "usage", None):
                usage = dict(event.usage)
            if getattr(event, "kind", None) == StreamEventKind.TEXT_DELTA:
                text_chars += len(getattr(event, "text", "") or "")
            if getattr(event, "kind", None) == StreamEventKind.DONE:
                est = 0
                if not usage:
                    system = str(kwargs.get("system") or "")
                    msgs = kwargs.get("messages") or []
                    msg_chars = sum(len(getattr(m, "content", "") or "") for m in msgs)
                    est = len(system) + msg_chars + text_chars
                self._log(usage=usage or None, estimate_chars=est)
            yield event

    async def test_connection(self) -> tuple[bool, str]:
        ok, detail = await self._inner.test_connection()
        if ok:
            self._log(estimate_chars=0, agent="key_test")
        return ok, detail


def make_provider(
    name: str,
    api_key: str,
    model: str = "",
    *,
    thinking_effort: str = "off",
) -> LLMProvider:
    name = (name or "").strip().lower()
    model = (model or "").strip()
    if not name:
        raise ValueError(
            "No LLM provider — install a gateway from Settings → Store → Gateways "
            "and pick a model"
        )
    if not model:
        raise ValueError("Model is required — load models from your API key in Settings")

    from backend.uefn_plugins.host import get_llm_provider_registration

    reg = get_llm_provider_registration(name)
    if reg is None:
        raise ValueError(
            f"Unknown provider {name!r} — install a gateway from Settings → Store → Gateways"
        )
    if name not in gateway_providers():
        raise ValueError(
            f"{name!r} gateway is not installed or enabled — "
            "install it from Settings → Store → Gateways"
        )
    factory = reg["factory"]
    try:
        inner = factory(api_key, model, thinking_effort=thinking_effort)
    except TypeError:
        inner = factory(api_key, model)
    return _UsageTrackingProvider(inner, provider=name, model=model)  # type: ignore[return-value]
