"""The dedicated (stdio) tool server beats notifications/progress while a call runs."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from mcp.server.lowlevel.server import request_ctx

from backend.bridge import shared_mcp
from backend.server import ProtectedFastMCP

EVERY = 0.01


class _Session:
    def __init__(self, fail_after: int | None = None):
        self.notes: list[tuple] = []
        self.fail_after = fail_after

    async def send_progress_notification(self, token, progress, total=None, message=None,
                                         related_request_id=None):
        if self.fail_after is not None and len(self.notes) >= self.fail_after:
            import anyio

            raise anyio.ClosedResourceError
        self.notes.append((token, progress, message, related_request_id))


def _server(monkeypatch, wait_s: float, result="answer", until=None):
    server = ProtectedFastMCP("progress-test")

    async def slow(name, arguments):
        # ``until``: run until that many intervals have passed, by beat count, not wall
        # time (Windows sleeps round up to ~15 ms); ``wait_s`` caps it either way.
        loop = asyncio.get_running_loop()
        end = loop.time() + wait_s
        while loop.time() < end and not (until and until()):
            await asyncio.sleep(EVERY / 2)
        return result

    monkeypatch.setattr(server, "_guarded_call", slow)
    monkeypatch.setattr(shared_mcp, "PROGRESS_EVERY_S", EVERY)
    return server


async def _call(server, session, token):
    meta = SimpleNamespace(progressToken=token) if token is not None else None
    ctx_token = request_ctx.set(SimpleNamespace(meta=meta, session=session, request_id=7))
    try:
        return await server.call_tool("ducky_ask_user", {})
    finally:
        request_ctx.reset(ctx_token)


def test_a_long_call_with_a_progress_token_beats_and_returns(monkeypatch):
    session = _Session()
    server = _server(monkeypatch, 10.0, until=lambda: len(session.notes) >= 35)
    assert asyncio.run(_call(server, session, "tok")) == "answer"
    assert len(session.notes) >= 30
    assert [n[1] for n in session.notes] == list(range(1, len(session.notes) + 1))
    assert all(n[0] == "tok" and n[3] == 7 and "ducky_ask_user still running" in n[2]
               for n in session.notes)


def test_no_progress_token_sends_nothing(monkeypatch):
    server = _server(monkeypatch, 5 * EVERY)
    session = _Session()
    assert asyncio.run(_call(server, session, None)) == "answer"
    assert session.notes == []


def test_beats_stop_when_the_call_returns(monkeypatch):
    server = _server(monkeypatch, 3 * EVERY)
    session = _Session()

    async def run():
        out = await _call(server, session, 1)
        seen = len(session.notes)
        await asyncio.sleep(10 * EVERY)
        return out, seen

    out, seen = asyncio.run(run())
    assert out == "answer"
    assert len(session.notes) == seen


def test_a_gone_client_ends_the_beats_not_the_call(monkeypatch):
    server = _server(monkeypatch, 10 * EVERY)
    session = _Session(fail_after=2)
    assert asyncio.run(_call(server, session, "tok")) == "answer"
    assert len(session.notes) == 2


def test_outside_a_request_the_call_runs_without_beats(monkeypatch):
    server = _server(monkeypatch, 2 * EVERY)
    assert asyncio.run(server.call_tool("ducky_ask_user", {})) == "answer"
