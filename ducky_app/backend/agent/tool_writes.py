"""A tool call's work kept off the event loop every agent shares: its writes (sidecar journal,
spill file, receipts), each of which held up every other agent's call, and its Stop wait."""

from __future__ import annotations

import asyncio
import functools
import threading
import time
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


# Stop signals of calls in flight. A turn's signal (runner._CancelBridge) has only is_set()
# and is partly a threading.Event, so nothing can await it: one thread checks them all and
# wakes a call's loop only when its Stop is set. Each call polling its own (every 0.2 s, on
# the loop every agent shares) cost 5 loop wakeups a second per call in flight.
# ponytail: one 0.1 s check for every Stop; per-signal callbacks if the bridge ever gets them.
CANCEL_CHECK_S = 0.1
_stops: dict[int, tuple[Any, asyncio.AbstractEventLoop, asyncio.Future[Any]]] = {}
_stops_lock = threading.Lock()
_stops_added = threading.Event()
_stop_watcher: threading.Thread | None = None


def _fire(fut: asyncio.Future[Any]) -> None:
    if not fut.done():
        fut.set_result(True)


def _watch_stops() -> None:
    while True:
        with _stops_lock:
            items = list(_stops.items())
        if not items:
            _stops_added.wait()
            _stops_added.clear()
            continue
        for key, (signal, loop, fut) in items:
            try:
                hit = bool(signal.is_set())
            except Exception:
                hit = False
            if hit:
                with _stops_lock:
                    _stops.pop(key, None)
                try:
                    loop.call_soon_threadsafe(_fire, fut)
                except RuntimeError:
                    pass  # that loop is gone
        time.sleep(CANCEL_CHECK_S)


def _watch_stop(signal: Any) -> asyncio.Future[Any]:
    """A future on this loop that is set once ``signal.is_set()`` turns true."""
    global _stop_watcher
    fut = asyncio.get_running_loop().create_future()
    with _stops_lock:
        _stops[id(fut)] = (signal, asyncio.get_running_loop(), fut)
        if _stop_watcher is None:
            _stop_watcher = threading.Thread(target=_watch_stops, name="tool-stop-watch", daemon=True)
            _stop_watcher.start()
    _stops_added.set()
    return fut


async def _end(task: asyncio.Future[Any]) -> None:
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


async def await_cancellable(coro: Any, *, cancel_event: Any | None, timeout: float = 180.0) -> Any:
    """Await ``coro``; Stop (``cancel_event.is_set()``) ends it within about 0.1 s and raises
    CancelledError, the time limit raises TimeoutError. The loop wakes for neither until then."""
    task = asyncio.ensure_future(coro)
    if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
        await _end(task)
        raise asyncio.CancelledError("Cancelled")
    stop = _watch_stop(cancel_event) if cancel_event is not None and hasattr(cancel_event, "is_set") else None
    try:
        waiters = {task} if stop is None else {task, stop}
        done, _ = await asyncio.wait(waiters, timeout=max(timeout, 1.0), return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            return task.result()
        await _end(task)
        if stop is not None and stop in done:
            raise asyncio.CancelledError("Cancelled")
        raise asyncio.TimeoutError(f"Tool timed out after {timeout}s")
    except asyncio.CancelledError:
        raise
    except Exception:
        if not task.done():
            await _end(task)
        raise
    finally:
        if stop is not None:
            with _stops_lock:
                _stops.pop(id(stop), None)
            stop.cancel()
