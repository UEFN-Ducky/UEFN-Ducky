"""Exercise real AnyIO scopes across MCP initialization, requests and teardown."""

from __future__ import annotations

import asyncio
import gc
from contextlib import asynccontextmanager
from types import SimpleNamespace

import anyio
import pytest
from mcp.types import Tool

from backend.mcp_plugins import client_pool as cp


@pytest.fixture
def transport(monkeypatch):
    state = SimpleNamespace(
        opened=[], closed=[], scopes=[], calls=[], fail_init=False, hang_init=False,
        fail_list_once=False, initializing=None, calling=None,
    )

    @asynccontextmanager
    async def scoped(label):
        host = asyncio.current_task()
        state.opened.append((label, host))
        try:
            async with anyio.create_task_group() as group:
                state.scopes.append(group.cancel_scope)
                group.start_soon(anyio.sleep_forever)
                try:
                    yield
                finally:
                    assert asyncio.current_task() is host
                    group.cancel_scope.cancel()
        finally:
            state.closed.append((label, asyncio.current_task()))

    @asynccontextmanager
    async def stdio(params):
        async with scoped("transport"):
            yield (None, None)

    class Session:
        def __init__(self, *_args):
            self.context = scoped("session")

        async def __aenter__(self):
            await self.context.__aenter__()
            return self

        async def __aexit__(self, *args):
            return await self.context.__aexit__(*args)

        async def initialize(self):
            if state.initializing is not None:
                state.initializing.set()
            if state.fail_init:
                raise ValueError("diagnostic handshake rejected")
            if state.hang_init:
                await anyio.sleep_forever()

        async def list_tools(self):
            if state.fail_list_once:
                state.fail_list_once = False
                raise ConnectionError("transport disconnected")
            return SimpleNamespace(tools=[Tool(name="echo", inputSchema={"type": "object"})])

        async def call_tool(self, name, args):
            state.calls.append(name)
            if name == "block":
                state.calling.set()
                await anyio.sleep_forever()
            return SimpleNamespace(content=[])

    monkeypatch.setattr(cp, "stdio_client", stdio)
    monkeypatch.setattr(cp, "ClientSession", Session)
    monkeypatch.setattr(cp, "load_plugin_manifest", lambda pid: {"id": pid, "tool_prefix": pid})
    monkeypatch.setattr(cp, "resolve_server_block", lambda manifest: {"type": "stdio", "command": "unused", "args": []})
    return state


def assert_closed(state):
    assert len(state.opened) == len(state.closed)
    assert sorted((label, id(task)) for label, task in state.opened) == sorted(
        (label, id(task)) for label, task in state.closed
    )
    assert all(not scope._tasks for scope in state.scopes)


def test_connections_have_independent_owners_and_close_from_later_tasks(transport):
    pool = cp.PluginClientPool(own_loop=True)
    try:
        assert len(pool.run_sync(pool.list_tools_for_plugin("one"))) == 1
        assert len(pool.run_sync(pool.list_tools_for_plugin("two"))) == 1
        hosts = {id(host) for _, host in transport.opened}
        assert len(hosts) == 2
        pool.close_plugin("one")
        assert len(pool.run_sync(pool.list_tools_for_plugin("two"))) == 1
        pool.close_plugin("two")
        assert_closed(transport)
    finally:
        pool.shutdown_sync()


@pytest.mark.parametrize("mode", ["rejected", "timeout", "caller_cancelled", "anyio_timeout"])
def test_partial_initialization_always_closes_scopes(transport, monkeypatch, mode):
    async def run():
        pool = cp.PluginClientPool()
        transport.initializing = asyncio.Event()
        transport.fail_init = mode == "rejected"
        transport.hang_init = mode != "rejected"
        if mode == "timeout":
            monkeypatch.setattr(cp, "_CONNECT_TIMEOUT_SEC", 0.02)
        try:
            if mode == "caller_cancelled":
                pending = asyncio.create_task(pool.list_tools_for_plugin("one"))
                await transport.initializing.wait()
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            elif mode == "anyio_timeout":
                with pytest.raises(TimeoutError), anyio.fail_after(0.02):
                    await pool.list_tools_for_plugin("one")
            else:
                with pytest.raises(Exception):
                    await pool.list_tools_for_plugin("one")
            await asyncio.sleep(0)
            assert_closed(transport)
            assert not pool._owners
        finally:
            await pool.shutdown_all()

    asyncio.run(run())


def test_failed_initialization_leaves_no_idle_cancellation_callbacks(transport, monkeypatch):
    from anyio._backends._asyncio import CancelScope

    calls = []
    original = CancelScope._deliver_cancellation

    def counted(self, origin):
        calls.append(1)
        return original(self, origin)

    monkeypatch.setattr(CancelScope, "_deliver_cancellation", counted)

    async def run():
        pool = cp.PluginClientPool()
        transport.fail_init = True
        for _ in range(10):
            with pytest.raises(Exception):
                await pool.list_tools_for_plugin("one")
        gc.collect()
        for _ in range(3):
            await asyncio.sleep(0)
        count = len(calls)
        await asyncio.sleep(0.03)
        assert len(calls) == count
        assert_closed(transport)
        await pool.shutdown_all()

    asyncio.run(run())


def test_request_cancellation_keeps_connection_usable(transport):
    async def run():
        pool = cp.PluginClientPool()
        transport.calling = asyncio.Event()
        conn = await pool._get_or_create("one")
        session = await pool._ensure_session(conn)
        active = asyncio.create_task(session.call_tool("block", {}))
        await transport.calling.wait()
        queued = asyncio.create_task(session.call_tool("never", {}))
        await asyncio.sleep(0)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        active.cancel()
        with pytest.raises(asyncio.CancelledError):
            await active
        await asyncio.wait_for(session.call_tool("after", {}), 1)
        assert transport.calls == ["block", "after"]
        assert len(transport.opened) == 2
        await pool.shutdown_all()
        assert_closed(transport)

    asyncio.run(run())


def test_shutdown_wakes_active_queued_and_backpressured_requests(transport):
    async def run():
        pool = cp.PluginClientPool()
        transport.calling = asyncio.Event()
        session = await pool._ensure_session(await pool._get_or_create("one"))
        active = asyncio.create_task(session.call_tool("block", {}))
        await transport.calling.wait()
        queued = [asyncio.create_task(session.call_tool("queued", {})) for _ in range(40)]
        for _ in range(3):
            await asyncio.sleep(0)
        await asyncio.wait_for(pool.shutdown_all(), 1)
        results = await asyncio.wait_for(asyncio.gather(active, *queued, return_exceptions=True), 1)
        assert all(isinstance(result, ConnectionError) for result in results)
        assert transport.calls == ["block"]
        assert_closed(transport)

    asyncio.run(run())


def test_shutdown_interrupts_an_in_progress_handshake(transport):
    async def run():
        pool = cp.PluginClientPool()
        transport.hang_init = True
        transport.initializing = asyncio.Event()
        pending = asyncio.create_task(pool.list_tools_for_plugin("one"))
        await transport.initializing.wait()
        await asyncio.wait_for(pool.shutdown_all(), 1)
        with pytest.raises(ConnectionError):
            await pending
        assert_closed(transport)

    asyncio.run(run())


def test_dead_transport_reconnects_without_cross_task_cleanup(transport):
    async def run():
        pool = cp.PluginClientPool()
        transport.fail_list_once = True
        assert len(await pool.list_tools_for_plugin("one")) == 1
        assert len(transport.opened) == 4
        assert len(transport.closed) == 2
        await pool.shutdown_all()
        assert_closed(transport)

    asyncio.run(run())


def test_eviction_skips_in_flight_work(transport):
    async def run():
        pool = cp.PluginClientPool()
        transport.calling = asyncio.Event()
        session = await pool._ensure_session(await pool._get_or_create("one"))
        active = asyncio.create_task(session.call_tool("block", {}))
        await transport.calling.wait()
        assert await pool.evict_idle(max_idle_sec=-1) == 0
        active.cancel()
        await asyncio.gather(active, return_exceptions=True)
        for _ in range(3):
            await asyncio.sleep(0)
        assert await pool.evict_idle(max_idle_sec=-1) == 1
        assert_closed(transport)
        await pool.shutdown_all()

    asyncio.run(run())


def test_sync_timeout_cancels_the_submitted_coroutine():
    pool = cp.PluginClientPool(own_loop=True)
    try:
        import threading

        cancelled = threading.Event()

        async def block():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with pytest.raises(TimeoutError):
            pool.run_sync(block(), timeout=0.03)
        assert cancelled.wait(1)
    finally:
        pool.shutdown_sync()
