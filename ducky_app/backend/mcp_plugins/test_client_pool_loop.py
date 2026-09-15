"""Owned pool loop: throwaway asyncio.run loops must not bind nested MCP sessions."""

from __future__ import annotations

import asyncio
import threading

from backend.mcp_plugins.client_pool import PluginClientPool, PluginConnection


def test_two_throwaway_loops_share_owned_loop() -> None:
    pool = PluginClientPool(own_loop=True)
    try:
        async def probe() -> int:
            return id(asyncio.get_running_loop())

        a = asyncio.run(pool.run_async(probe()))
        b = asyncio.run(pool.run_async(probe()))
        assert a == b == id(pool._loop)
    finally:
        pool.shutdown_sync()


def test_init_under_running_loop_binds_lock_to_owned_loop() -> None:
    created: dict[str, PluginClientPool] = {}

    async def make() -> int:
        pool = PluginClientPool(own_loop=True)
        created["pool"] = pool

        async def probe() -> int:
            assert pool._pool_lock is not None
            async with pool._pool_lock:
                return id(asyncio.get_running_loop())

        return await pool.run_async(probe())

    loop_id = asyncio.run(make())
    pool = created["pool"]
    try:
        assert loop_id == id(pool._loop)
    finally:
        pool.shutdown_sync()


def test_close_plugin_from_thread_with_no_loop() -> None:
    pool = PluginClientPool(own_loop=True)
    try:
        conn = PluginConnection(plugin_id="demo", manifest={})
        conn.session = object()
        pool._connections["demo"] = conn
        err: list[BaseException] = []

        def worker() -> None:
            try:
                asyncio.get_running_loop()
                err.append(RuntimeError("worker unexpectedly has a loop"))
            except RuntimeError:
                pass
            pool.close_plugin("demo")

        t = threading.Thread(target=worker)
        t.start()
        t.join(5)
        assert not t.is_alive()
        assert not err
        assert "demo" not in pool._connections
        assert conn.session is None
    finally:
        pool.shutdown_sync()


def test_run_sync_from_running_loop_does_not_asyncio_run() -> None:
    pool = PluginClientPool(own_loop=True)
    try:
        async def inner() -> int:
            return 42

        async def outer() -> int:
            return pool.run_sync(inner())

        assert asyncio.run(outer()) == 42
    finally:
        pool.shutdown_sync()
