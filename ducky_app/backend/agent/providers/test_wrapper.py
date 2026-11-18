"""The provider wrapper's gateway counters: words only, never the error text."""

from __future__ import annotations

import asyncio

from backend.agent import providers
from backend.agent.providers.base import StreamEvent, StreamEventKind as K
from frontend import usage_events as ev
from frontend import usage_stats


class _Mismatch:
    async def stream_turn(self, **_kw):
        raise RuntimeError("400 INVALID_ARGUMENT: function response parts must match my_secret_tool calls")
        yield StreamEvent(kind=K.DONE)  # an async generator


def test_gateway_function_response_mismatch_is_one_turn_fail(monkeypatch) -> None:
    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(usage_stats, "track", lambda name, **props: sent.append((name, ev.validate(name, props))))
    monkeypatch.setattr(usage_stats, "_gateway_v", lambda provider: "1.0.57")
    monkeypatch.setattr(providers._UsageTrackingProvider, "_log", lambda *_a, **_k: None)
    p = providers._UsageTrackingProvider(_Mismatch(), provider="gemini", model="m")

    async def go():
        return [e async for e in p.stream_turn(messages=[], system="")]

    try:
        asyncio.run(go())
    except RuntimeError:
        pass
    assert sent == [("gateway.turn_fail", {"provider": "gemini", "stage": "before_output", "reason": "fc_mismatch",
                                           "retried": "0", "switched": "n", "plugin_v": "1.0.57"})]
    assert "secret" not in str(sent)
