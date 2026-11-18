"""A tool call's file and database writes (sidecar journal, spill file, receipts), run off
the event loop every agent shares: each one held up every other agent's call while it wrote."""

from __future__ import annotations

import asyncio
import functools
from typing import Any, Callable


async def off_loop(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """``fn(*args, **kwargs)`` on a worker thread; the caller's contextvars (identity, mode) go with it."""
    return await asyncio.to_thread(functools.partial(fn, *args, **kwargs))


def record_sidecar(name: str, args: dict[str, Any], text: str, *, ok: bool) -> None:
    """Journal a plugin / Epic result. Never fails the tool."""
    try:
        from backend.workspace.tool_record import record_tool_result

        record_tool_result(name, args, text, ok=ok)
    except Exception:
        pass


def _spill_then_record(name: str, args: dict[str, Any], text: str) -> str:
    from backend.agent.tool_spills import inline_or_spill

    data = inline_or_spill(name, text)
    record_sidecar(name, args, data, ok=True)
    return data


async def spill_and_record(name: str, args: dict[str, Any], text: str) -> str:
    """A good result: spilled to a file when large, journaled; returns what the agent gets."""
    return await off_loop(_spill_then_record, name, args, text)
