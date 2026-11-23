"""A tool call's spill, sidecar and receipt writes run off the shared event loop."""

from __future__ import annotations

import asyncio
import contextvars
import threading

from backend.agent import tool_writes

_who = contextvars.ContextVar("who", default="")


def test_the_spill_write_runs_off_the_loop(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def spill(name, text, limit=0):
        seen["spill"] = threading.get_ident()
        seen["who"] = _who.get()
        return text

    monkeypatch.setattr("backend.agent.tool_spills.inline_or_spill", spill)
    monkeypatch.setattr("backend.workspace.tool_record.record_tool_result",
                        lambda *a, **k: seen.setdefault("record", threading.get_ident()))

    async def main() -> str:
        _who.set("run-7")
        seen["loop"] = threading.get_ident()
        return await tool_writes.spill_and_record("demo__ping", {}, "pong")

    assert asyncio.run(main()) == "pong"
    assert seen["spill"] != seen["loop"] and seen["record"] != seen["loop"]
    assert seen["who"] == "run-7"  # the caller's identity went with it


def test_the_result_is_the_same_as_before(monkeypatch, tmp_path) -> None:
    import json

    from backend.agent import tool_spills

    monkeypatch.setattr("backend.workspace.tool_record.record_tool_result", lambda *a, **k: None)
    monkeypatch.setattr(tool_spills, "tool_spills_dir", lambda for_write=False: tmp_path)
    assert asyncio.run(tool_writes.spill_and_record("demo__big", {}, "small result")) == "small result"
    big = "x" * 400_000
    direct = json.loads(tool_spills.inline_or_spill("demo__big", big))
    via = json.loads(asyncio.run(tool_writes.spill_and_record("demo__big", {}, big)))
    # Each spill names its own file; everything else in the stub is the same.
    for stub in (direct, via):
        stub.pop("path"), stub.pop("name")
    assert via == direct and via["chars"] == 400_000
    assert len(list(tmp_path.glob("demo__big_*.txt"))) == 2


class _Stop:
    """Like runner._CancelBridge: is_set() only."""

    def __init__(self) -> None:
        self._ev = threading.Event()

    def is_set(self) -> bool:
        return self._ev.is_set()


async def _timers_due_within(seconds: float) -> list[float]:
    """Inside a wrapped call: every live timer on this loop due in under ``seconds``."""
    await asyncio.sleep(0.05)  # the wrapper has set up its wait by now
    loop = asyncio.get_running_loop()
    left = [h.when() - loop.time() for h in loop._scheduled if not h.cancelled()]  # type: ignore[attr-defined]
    return [t for t in left if t < seconds]


def test_a_call_without_a_stop_event_never_wakes_early() -> None:
    import pytest

    # A poll (0.2 s) shows up as a timer due soon. Counting loop wakeups instead measured
    # how early a busy Windows runner's timers fire: (8, 4) on CI.
    for signal in (None, _Stop()):
        soon = asyncio.run(tool_writes.await_cancellable(_timers_due_within(1.0), cancel_event=signal))
        assert soon == [], soon
    with pytest.raises(asyncio.TimeoutError, match="timed out after 1"):
        asyncio.run(tool_writes.await_cancellable(asyncio.sleep(5), cancel_event=None, timeout=1.0))


def test_stop_still_ends_the_call_within_300_ms() -> None:
    import time

    import pytest

    stop = _Stop()
    ended = threading.Event()

    async def long_call() -> None:
        try:
            await asyncio.sleep(30)
        finally:
            ended.set()

    threading.Timer(0.2, stop._ev.set).start()
    started = time.monotonic()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(tool_writes.await_cancellable(long_call(), cancel_event=stop, timeout=60))
    assert time.monotonic() - started < 0.2 + 0.3
    assert ended.is_set()  # the call itself was stopped
    assert not tool_writes._stops  # nothing left watched
    # Already set: the call is stopped before it waits.
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(tool_writes.await_cancellable(asyncio.sleep(30), cancel_event=stop))


def test_a_failing_journal_never_fails_the_tool(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("backend.workspace.tool_record.record_tool_result", boom)
    asyncio.run(tool_writes.off_loop(tool_writes.record_sidecar, "demo__ping", {}, "x", ok=False))
