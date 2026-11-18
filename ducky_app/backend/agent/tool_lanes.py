"""Where a tool reached through the tool layer runs: off the loop in a shared tool server."""

from __future__ import annotations

import asyncio
import contextvars
import queue
import threading
from typing import Any


class _PluginLane:
    """One thread that runs a plugin's sync tool calls, one after another
    (:func:`call_registered_tool`)."""

    def __init__(self, plugin_id: str) -> None:
        self._jobs: queue.SimpleQueue[Any] = queue.SimpleQueue()
        threading.Thread(target=self._work, daemon=True, name=f"plugin-tools:{plugin_id}"[:60]).start()

    def _work(self) -> None:
        while True:
            self._jobs.get()()  # a job settles its own call, whatever it raises

    def run(self, job: Any) -> None:
        self._jobs.put(job)


_plugin_lanes: dict[str, _PluginLane] = {}
_plugin_lanes_guard = threading.Lock()


def _runs_on_the_loop(mcp: Any, name: str) -> bool:
    """A sync tool: FastMCP calls its function on the running loop, start to finish."""
    registered = getattr(getattr(mcp, "_tool_manager", None), "_tools", None)
    tool = registered.get(name) if isinstance(registered, dict) else None
    return tool is not None and getattr(tool, "is_async", True) is False


def _shared_tool_server() -> bool:
    """This process serves several agents' tools on one loop (an agent host, a shared daemon)."""
    import sys

    shared = sys.modules.get("backend.bridge.shared_mcp")  # never imported: it is not one
    return bool(shared is not None and shared.serving())


async def call_registered_tool(mcp: Any, name: str, args: dict[str, Any]) -> Any:
    """``mcp.call_tool``; in a shared tool server, a sync tool's work runs off the loop.

    ducky_call_tool is async, so the server runs it on its one loop, and FastMCP then called
    a sync inner tool's function right there, start to finish. Every agent's tool calls
    start and finish on that loop, and so does much of starting a turn. Nov 18 2026: one
    chat's road_network_pipeline calls (45-78 s each) held two file edits of another chat
    for 54 s, and kept that chat's next turn from starting for 5 min 50 s. A sync tool
    called by its own name already has a thread of its own (shared_mcp._call_tool).

    A plugin's calls made this way ran one after another until now (the loop saw to it), so
    they still do: one thread per plugin takes them in turn. Core tools get a thread each,
    as when they are called by name.
    """
    if not (_shared_tool_server() and _runs_on_the_loop(mcp, name)):
        return await mcp.call_tool(name, args)
    loop = asyncio.get_running_loop()
    done: asyncio.Future[Any] = loop.create_future()
    ctx = contextvars.copy_context()  # the calling agent's identity and mode

    def _settle(value: Any, error: BaseException | None) -> None:
        if done.done():
            return  # given up meanwhile (Stop, or its time limit)
        if error is not None:
            done.set_exception(error)
        else:
            done.set_result(value)

    def _job() -> None:
        try:
            value, error = ctx.run(asyncio.run, mcp.call_tool(name, args)), None
        except BaseException as exc:  # noqa: BLE001 - handed to the awaiting call
            value, error = None, exc
        try:
            loop.call_soon_threadsafe(_settle, value, error)
        except RuntimeError:
            pass  # the loop is gone (the server is stopping)

    from backend.uefn_plugins.plugin_host.tool_registry import plugin_for_tool

    owner = plugin_for_tool(name)
    if owner == "ducky":
        threading.Thread(target=_job, daemon=True, name=f"tool:{name}"[:60]).start()
    else:
        with _plugin_lanes_guard:
            lane = _plugin_lanes.get(owner)
            if lane is None:
                lane = _plugin_lanes[owner] = _PluginLane(owner)
        lane.run(_job)
    return await done
