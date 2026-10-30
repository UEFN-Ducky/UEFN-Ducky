"""LLM providers for embedded agent.

Gateways come from Store plugins via ``llm.providers`` + ``api.register_llm_provider``.
Host keeps only the protocol and registry lookup.

Every provider from ``make_provider`` is wrapped so ``stream_turn`` / ``test_connection``
always append to Settings → LLM usage — no caller can forget.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any, Callable

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
            if row.get("chat") is False:
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


_QUOTA_RE = re.compile(r"\b429\b|rate[_ ]?limit|insufficient_quota|quota", re.IGNORECASE)
_OUTPUT_KINDS = (StreamEventKind.TEXT_DELTA, StreamEventKind.TOOL_CALLS, StreamEventKind.THINKING)


def _account_for_key(provider: str, api_key: str) -> str | None:
    """The account whose saved key this is, when the provider has 2+ accounts."""
    try:
        from backend.agent import accounts

        rows = accounts.list_accounts(provider)
        if len(rows) < 2 or not api_key:
            return None
        return next((r["id"] for r in rows if accounts.account_key(provider, r["id"]) == api_key), None)
    except Exception:
        return None


class _UsageTrackingProvider:
    """Wrap any gateway provider so every key use hits the usage ledger, and a key out of
    quota before the reply starts moves the turn to the provider's next account."""

    def __init__(
        self,
        inner: Any,
        *,
        provider: str,
        model: str,
        account: str | None = None,
        rebuild: Callable[[str], Any] | None = None,
        is_quota_error: Callable[[str], bool] | None = None,
        conv_id: str = "",
    ) -> None:
        self._conv_id = conv_id
        self._inner = inner
        self._provider = provider
        self._model = model
        self._account = account
        self._rebuild = rebuild
        self._is_quota = is_quota_error or (lambda text: bool(_QUOTA_RE.search(text or "")))

    def _quota(self, text: str) -> bool:
        if self._account is None:
            return False
        try:
            return bool(self._is_quota(str(text or "")))
        except Exception:
            return False

    def _switch_account(self, tried: set[str]) -> bool:
        """Mark this account full; move to the next one with a key not tried this turn."""
        from backend.agent import accounts

        try:
            accounts.mark_exhausted(self._provider, self._account)
            if self._rebuild is None:
                return False
            nxt = accounts.pick_account(
                self._provider,
                conv_id=self._conv_id,
                is_full=lambda i: i in tried or not accounts.account_key(self._provider, i),
            )
            key = accounts.account_key(self._provider, nxt)
            if nxt in tried or not key:
                return False
            self._inner = self._rebuild(key)
        except Exception:
            return False
        self._account = nxt
        tried.add(nxt)
        return True

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
        logged = False

        def _estimate() -> int:
            if usage:
                return 0
            system = str(kwargs.get("system") or "")
            msgs = kwargs.get("messages") or []
            msg_chars = sum(len(getattr(m, "content", "") or "") for m in msgs)
            return len(system) + msg_chars + text_chars

        tried = {self._account} if self._account else set()
        started = False
        try:
            while True:
                quota_event = None
                stream = self._inner.stream_turn(**kwargs)
                try:
                    async for event in stream:
                        kind = getattr(event, "kind", None)
                        if not started and kind == StreamEventKind.ERROR and self._quota(event.error):
                            quota_event = event
                            break
                        started = started or kind in _OUTPUT_KINDS
                        if getattr(event, "usage", None):
                            usage = dict(event.usage)
                        if kind == StreamEventKind.TEXT_DELTA:
                            text_chars += len(getattr(event, "text", "") or "")
                        if kind == StreamEventKind.DONE:
                            self._log(usage=usage or None, estimate_chars=_estimate())
                            logged = True
                        elif kind == StreamEventKind.ERROR and started and self._quota(event.error):
                            self._switch_account(set(tried))  # next turn starts on the next account
                        yield event
                except Exception as exc:
                    if not self._quota(str(exc)):
                        raise
                    if started:
                        self._switch_account(set(tried))
                        raise
                    if not self._switch_account(tried):
                        raise
                    continue
                finally:
                    aclose = getattr(stream, "aclose", None)
                    if aclose is not None:
                        await aclose()
                if quota_event is None:
                    return
                if not self._switch_account(tried):
                    yield quota_event
                    return
        finally:
            if not logged:
                self._log(usage=usage or None, estimate_chars=_estimate())

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
    conv_id: str = "",
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

    def build(key: str) -> Any:
        try:
            return factory(key, model, thinking_effort=thinking_effort)
        except TypeError:
            return factory(key, model)

    quota_fn = reg.get("is_quota_error")
    return _UsageTrackingProvider(  # type: ignore[return-value]
        build(api_key),
        provider=name,
        model=model,
        account=_account_for_key(name, api_key),
        rebuild=build,
        is_quota_error=quota_fn if callable(quota_fn) else None,
        conv_id=conv_id,
    )
