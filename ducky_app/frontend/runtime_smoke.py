"""Headless packaged-runtime check; uses synthetic data and no user configuration."""
from __future__ import annotations

import asyncio
import importlib.metadata
import json
import multiprocessing
import platform
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import anyio


async def _check_mcp() -> dict:
    from backend.mcp_plugins import client_pool as cp
    from backend.mcp_plugins.session_owner import OwnedSession

    scopes = []
    exited = []
    failed = [True]
    original_stdio, original_session, original_resolve = cp.stdio_client, cp.ClientSession, cp.resolve_server_block

    @asynccontextmanager
    async def transport(params):
        host = asyncio.current_task()
        async with anyio.create_task_group() as group:
            scopes.append(group.cancel_scope)
            group.start_soon(anyio.sleep_forever)
            try:
                yield None, None
            finally:
                assert asyncio.current_task() is host
                group.cancel_scope.cancel()
                exited.append(True)

    class Session:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def initialize(self):
            if failed[0]:
                raise ValueError("synthetic initialization failure")

        async def call_tool(self, name, args):
            return args

    cp.stdio_client, cp.ClientSession = transport, Session
    cp.resolve_server_block = lambda manifest: {"type": "stdio", "command": "unused", "args": []}
    pool = cp.PluginClientPool()
    try:
        for _ in range(5):
            conn = cp.PluginConnection("smoke", {})
            try:
                await pool._ensure_session(conn)
            except Exception:
                pass
            else:
                raise AssertionError("initialization should fail")
        failed[0] = False
        conn = cp.PluginConnection("smoke", {})
        pool._connections["smoke"] = conn
        session = await pool._ensure_session(conn)
        assert isinstance(session, OwnedSession)
        assert await session.call_tool("echo", {"value": 42}) == {"value": 42}
        # Close in a separate task, just like a later MCP caller or shutdown.
        await asyncio.create_task(pool.shutdown_all())
        for _ in range(3):
            await asyncio.sleep(0)
        assert len(exited) == 6
        assert all(not scope._tasks for scope in scopes)
        assert not pool._owners
        started, cpu = time.perf_counter(), time.process_time()
        await asyncio.sleep(0.5)
        wall, used = time.perf_counter() - started, time.process_time() - cpu
        assert used < 0.1, f"idle runtime used {used:.3f} CPU seconds during {wall:.3f}s"
        return {"contexts_closed": len(exited), "retained_scope_tasks": 0,
                "idle_wall_seconds": round(wall, 4), "idle_cpu_seconds": round(used, 4)}
    finally:
        await pool.shutdown_all()
        cp.stdio_client, cp.ClientSession, cp.resolve_server_block = original_stdio, original_session, original_resolve


def run(output: str) -> int:
    report: dict = {"ok": False, "python": platform.python_version()}
    try:
        return _run(output, report)
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        Path(output).write_text(json.dumps(report, indent=2), encoding="utf-8")
        return 1


def _run(output: str, report: dict) -> int:
    # 1.2.133 shipped without backend.agent.prompt in the PYZ (SyntaxError during
    # freeze). Import these first so a missing module fails smoke, not the GUI.
    import backend.agent.hard_rules  # noqa: F401
    import backend.agent.prompt  # noqa: F401
    import backend.agent.runner  # noqa: F401
    import backend.store.db  # noqa: F401
    from backend.workspace.diff_workers import DiffWorkers
    from backend.workspace.paths import line_delta

    report["versions"] = {
        name: importlib.metadata.version(name) for name in ("mcp", "anyio", "httpx")
    }
    report["mcp"] = asyncio.run(_check_mcp())
    workers = DiffWorkers()
    before = "".join(f"Value_{n}:int = {n}\n" for n in range(10000))
    pairs = [(before, before.replace(f"Value_{n}:int = {n}", f"Value_{n}:int = 99999")) for n in range(4)]
    try:
        with ThreadPoolExecutor(max_workers=4) as callers:
            results = list(callers.map(lambda pair: workers.compare(*pair), pairs))
        assert results == [line_delta(*pair) for pair in pairs]
        child_count = len(multiprocessing.active_children())
    finally:
        workers.close()
    assert not multiprocessing.active_children()
    report["cpu_workers"] = {"jobs": 4, "child_processes": child_count, "matching_results": True, "children_after_close": 0}
    report["ok"] = True
    Path(output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0
