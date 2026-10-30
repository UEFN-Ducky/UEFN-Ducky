"""Shared MCP daemon: no tool call hangs an agent forever, and a stuck one says where it waits.

Oct 18 2026: a user's Claude Code agent sat 29 minutes on ducky_find_tools ("Running tools
0/1"); agents wait on an MCP call with no limit of their own.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from backend.bridge import shared_mcp


class WatchMcp:
    _ducky_skip_plugin_wait = True

    def __init__(self, sync: set[str]) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.threads: list[str] = []
        self.run_ids: list[str] = []
        self.cancelled = threading.Event()
        names = {"ducky_find_tools", "slow_render", "blocking_sync", "ask_and_wait"}
        self._tool_manager = SimpleNamespace(_tools={
            n: SimpleNamespace(name=n, is_async=n not in sync, inputSchema={}) for n in names
        })

    async def list_tools(self):
        return list(self._tool_manager._tools.values())

    async def call_tool(self, name, arguments):
        from backend.workspace.identity import current

        ctx = current()
        self.run_ids.append(ctx.run_id if ctx else "")
        self.threads.append(threading.current_thread().name)
        self.started.set()
        if name == "ducky_find_tools":
            try:
                await _waits_on_a_catalog_that_never_answers()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
        if name == "slow_render":
            await asyncio.sleep(float((arguments or {}).get("seconds") or 0))
        if name in ("blocking_sync", "ask_and_wait"):
            self.release.wait(10)
        return [{"type": "text", "text": f"{name} done"}]


async def _waits_on_a_catalog_that_never_answers() -> None:
    await asyncio.Event().wait()


def _ident(run_id: str = "run-1"):
    return shared_mcp.identity_from_payload({"run_id": run_id, "conv_id": ""})


def _text(result: dict) -> str:
    return "".join(c.get("text", "") for c in result.get("content") or [])


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(shared_mcp, "QUICK_TOOL_LIMIT_S", 0.3)
    monkeypatch.setattr(shared_mcp, "STUCK_REPORT_S", 0.3)


def test_a_quick_tool_that_never_answers_is_stopped_and_says_where_it_waited(quick) -> None:
    from backend.store.repos import events

    mcp = WatchMcp(sync=set())
    started = time.monotonic()
    result = asyncio.run(shared_mcp._call_tool(mcp, "ducky_find_tools", {"query": "import_asset"}, _ident()))
    assert time.monotonic() - started < 5
    assert result["isError"] is True and "did not answer within" in _text(result)
    assert mcp.cancelled.is_set()  # the stuck call itself was stopped
    row = events.newest("tool_stuck", limit=1)[0]
    assert row["source"] == "ducky_find_tools"
    assert "_waits_on_a_catalog_that_never_answers" in row["payload"]["stack"]


def test_a_long_call_is_recorded_once_and_still_finishes(quick) -> None:
    from backend.store.repos import events

    mcp = WatchMcp(sync=set())
    result = asyncio.run(shared_mcp._call_tool(mcp, "slow_render", {"seconds": 0.8}, _ident()))
    assert not result.get("isError") and _text(result) == "slow_render done"
    rows = events.newest("tool_stuck", limit=5)
    assert [r["source"] for r in rows] == ["slow_render"]


def test_a_wait_by_design_is_recorded_but_not_counted_as_stuck(quick, monkeypatch) -> None:
    from backend.store.repos import events

    counted: list[tuple[float, str]] = []
    monkeypatch.setattr("frontend.usage_stats.stuck", lambda _agent, seconds, tool="": counted.append((seconds, tool)))
    mcp = WatchMcp(sync=set())
    asyncio.run(shared_mcp._call_tool(mcp, "slow_render", {"seconds": 0.6}, _ident()))
    assert len(counted) == 1 and counted[0][1] == "slow_render"  # the stat names the call that hung
    monkeypatch.setattr(shared_mcp, "WAITS_BY_DESIGN", frozenset({"slow_render"}))
    asyncio.run(shared_mcp._call_tool(mcp, "slow_render", {"seconds": 0.6}, _ident()))
    assert len(counted) == 1  # a question or a test suite is not a hang
    assert [r["source"] for r in events.newest("tool_stuck", limit=5)][:2] == ["slow_render", "slow_render"]


def test_a_sync_tool_runs_on_its_own_thread_with_the_calls_identity(quick) -> None:
    from backend.store.repos import events

    mcp = WatchMcp(sync={"blocking_sync"})

    async def scenario():
        call = asyncio.ensure_future(shared_mcp._call_tool(mcp, "blocking_sync", {}, _ident("run-sync")))
        await asyncio.sleep(0.6)  # past the stuck report: recorded with the thread's own stack
        mcp.release.set()
        return await call

    result = asyncio.run(scenario())
    assert _text(result) == "blocking_sync done"
    assert mcp.threads[0].startswith("shared-mcp-call:") and mcp.run_ids == ["run-sync"]
    stack = events.newest("tool_stuck", limit=1)[0]["payload"]["stack"]
    assert "call_tool" in stack and "wait" in stack


def test_waiting_sync_tools_never_hold_up_other_calls() -> None:
    """Calls that wait (questions, workflows, other agents) don't take the loop's pool:
    with every pool thread busy, a new call still runs at once."""
    mcp = WatchMcp(sync={"ask_and_wait"})
    loop = asyncio.new_event_loop()
    import concurrent.futures

    loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(max_workers=2))
    try:
        async def scenario():
            waits = [asyncio.ensure_future(shared_mcp._call_tool(mcp, "ask_and_wait", {}, _ident(f"w{i}")))
                     for i in range(4)]
            await asyncio.sleep(0.3)
            quick_call = await asyncio.wait_for(
                shared_mcp._call_tool(mcp, "slow_render", {"seconds": 0}, _ident("other")), timeout=3)
            mcp.release.set()
            await asyncio.gather(*waits)
            return quick_call

        assert _text(loop.run_until_complete(scenario())) == "slow_render done"
    finally:
        loop.close()


def test_an_agent_cancelling_the_call_still_stops_the_tool() -> None:
    mcp = WatchMcp(sync=set())

    async def scenario():
        call = asyncio.ensure_future(shared_mcp._call_tool(mcp, "ducky_find_tools", {}, _ident()))
        await asyncio.sleep(0.2)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call

    asyncio.run(scenario())
    assert mcp.cancelled.is_set()
