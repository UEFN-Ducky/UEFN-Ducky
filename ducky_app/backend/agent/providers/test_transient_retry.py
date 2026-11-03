from __future__ import annotations

import asyncio

import pytest

from backend.agent import providers
from backend.agent.providers.base import StreamEvent, StreamEventKind as K


class Fake:
    def __init__(self, plan: list[str]) -> None:
        self.plan, self.calls = plan, 0

    async def stream_turn(self, **_kw):
        step = self.plan[min(self.calls, len(self.plan) - 1)]
        self.calls += 1
        if step == "busy":
            yield StreamEvent(kind=K.ERROR, error="Ducky AI is busy right now")
            return
        if step == "raise":
            raise RuntimeError("503 Service Unavailable: overloaded")
        if step == "text_then_busy":
            yield StreamEvent(kind=K.TEXT_DELTA, text="partial")
            yield StreamEvent(kind=K.ERROR, error="Ducky AI is busy right now")
            return
        yield StreamEvent(kind=K.TEXT_DELTA, text="hi")
        yield StreamEvent(kind=K.DONE)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(providers, "_RETRY_DELAYS", (0.0, 0.0, 0.0))
    monkeypatch.setattr(providers._UsageTrackingProvider, "_log", lambda *_a, **_k: None)


def run(fake: Fake, **kw):
    p = providers._UsageTrackingProvider(fake, provider="ducky_ai", model="m")

    async def go():
        return [e async for e in p.stream_turn(messages=[], system="", **kw)]

    return asyncio.run(go())


def test_busy_twice_then_ok_gives_one_done():
    fake = Fake(["busy", "raise", "ok"])
    events = run(fake)
    assert fake.calls == 3
    assert [e.kind for e in events if e.kind != K.STATUS] == [K.TEXT_DELTA, K.DONE]
    assert [e.text for e in events if e.kind == K.STATUS][1].startswith("Ducky AI busy, retrying (2/3)")


def test_no_retry_after_output_started():
    fake = Fake(["text_then_busy", "ok"])
    events = run(fake)
    assert fake.calls == 1
    assert [e.kind for e in events] == [K.TEXT_DELTA, K.ERROR]


def test_gives_up_after_three_retries():
    fake = Fake(["busy"])
    events = run(fake)
    assert fake.calls == 4
    assert events[-1].kind == K.ERROR


def test_cancel_during_wait_stops():
    class Set:
        def is_set(self):
            return True

    fake = Fake(["busy", "ok"])
    events = run(fake, cancel_event=Set())
    assert fake.calls == 1 and events[-1].kind == K.ERROR


def test_retry_after_from_server_text():
    assert providers._retry_delay("Ducky AI is busy (overloaded) — retry in 5s", 0) == 5.0
    assert providers._retry_delay("busy", 1) == providers._RETRY_DELAYS[1]
