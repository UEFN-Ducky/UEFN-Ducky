"""Keep a nested MCP transport's contexts in one task for their entire lifetime."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

import anyio

log = logging.getLogger(__name__)
_MAX_PENDING = 16
_CLOSE_TIMEOUT = 10.0


@dataclass
class _Request:
    method: str
    args: tuple[Any, ...]
    timeout: float
    result: asyncio.Future[Any]
    scope: anyio.CancelScope | None = None

    def cancel(self, future: asyncio.Future[Any]) -> None:
        if future.cancelled() and self.scope is not None:
            self.scope.cancel()


class SessionOwner:
    """Serialize requests; only this task enters/exits transport and session scopes.

    An idle owner blocks on its queue. Cancelling a caller cancels its request,
    not the owner or another caller's work. Closing cancels the owner once and
    waits for its context managers to finish, including partially opened ones.
    """

    def __init__(self, plugin_id: str, open_session: Callable[[AsyncExitStack], Awaitable[Any]]) -> None:
        self.plugin_id = plugin_id
        self._open_session = open_session
        self._queue: asyncio.Queue[_Request] = asyncio.Queue(maxsize=_MAX_PENDING)
        self._ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._ready.add_done_callback(self._consume_ready_error)
        self._closing = False
        self._active: _Request | None = None
        self._task = asyncio.create_task(self._run(), name=f"mcp-session:{plugin_id}")
        self._task.add_done_callback(self._finished)

    @staticmethod
    def _consume_ready_error(future: asyncio.Future[None]) -> None:
        # A cancelled connection attempt may no longer be waiting on _ready.
        if not future.cancelled():
            future.exception()

    @property
    def alive(self) -> bool:
        return not self._closing and not self._task.done()

    @property
    def busy(self) -> bool:
        return self._active is not None or not self._queue.empty()

    async def start(self) -> None:
        await asyncio.shield(self._ready)

    async def request(self, method: str, *args: Any, timeout: float) -> Any:
        if not self.alive:
            raise ConnectionError(f"Nested MCP '{self.plugin_id}' session is closed")
        result = asyncio.get_running_loop().create_future()
        request = _Request(method, args, timeout, result)
        result.add_done_callback(request.cancel)
        putter = asyncio.create_task(self._queue.put(request))
        try:
            # Wake blocked submitters when the owner exits, even with a full queue.
            await asyncio.wait({putter, self._task}, return_when=asyncio.FIRST_COMPLETED)
            if not self.alive:
                result.cancel()
                raise ConnectionError(f"Nested MCP '{self.plugin_id}' session is closed")
            await putter
            return await result
        except BaseException:
            result.cancel()
            request.cancel(result)
            raise
        finally:
            if not putter.done():
                putter.cancel()
            # Consume the cancellation without sending it into the owner.
            with anyio.CancelScope(shield=True):
                await asyncio.gather(putter, return_exceptions=True)

    async def close(self) -> None:
        if not self._closing:
            self._closing = True
            if not self._task.done():
                self._task.cancel()
        # asyncio.wait does not send a second cancellation into context cleanup.
        done, _ = await asyncio.wait({self._task}, timeout=_CLOSE_TIMEOUT)
        if not done:
            log.error("Nested MCP %s cleanup exceeded %.1fs", self.plugin_id, _CLOSE_TIMEOUT)
            raise TimeoutError(f"Nested MCP '{self.plugin_id}' cleanup did not finish")

    async def _run(self) -> None:
        try:
            async with AsyncExitStack() as stack:
                session = await self._open_session(stack)
                self._ready.set_result(None)
                while True:
                    request = await self._queue.get()
                    if request.result.done():
                        continue
                    self._active = request
                    try:
                        with anyio.CancelScope() as scope:
                            request.scope = scope
                            try:
                                with anyio.fail_after(request.timeout):
                                    value = await getattr(session, request.method)(*request.args)
                            except Exception as exc:
                                if not request.result.done():
                                    request.result.set_exception(exc)
                            else:
                                if not request.result.done():
                                    request.result.set_result(value)
                        if not request.result.done():
                            request.result.cancel()
                    finally:
                        request.scope = None
                        # Preserve active work for _finished if transport failure
                        # cancelled the owner before the request could complete.
                        if request.result.done():
                            self._active = None
        except BaseException as exc:
            if not self._ready.done():
                if isinstance(exc, asyncio.CancelledError):
                    self._ready.set_exception(ConnectionError(f"Nested MCP '{self.plugin_id}' connection cancelled"))
                else:
                    self._ready.set_exception(exc)
            raise

    def _finished(self, task: asyncio.Task[None]) -> None:
        error = None if task.cancelled() else task.exception()
        if error is not None:
            # Exception text from transports can contain credential-bearing URLs.
            log.warning("Nested MCP %s owner stopped: %s", self.plugin_id, type(error).__name__)
        if not self._ready.done():
            self._ready.set_exception(ConnectionError(f"Nested MCP '{self.plugin_id}' connection closed"))
        pending = [self._active] if self._active is not None else []
        while not self._queue.empty():
            pending.append(self._queue.get_nowait())
        for request in pending:
            if not request.result.done():
                request.result.set_exception(ConnectionError(f"Nested MCP '{self.plugin_id}' connection closed"))
        self._active = None


class OwnedSession:
    """The caller-facing API contains no live transport or cancel scopes."""

    def __init__(self, owner: SessionOwner, *, list_timeout: float, tool_timeout: float) -> None:
        self.owner = owner
        self.list_timeout = list_timeout
        self.tool_timeout = tool_timeout

    async def list_tools(self) -> Any:
        return await self.owner.request("list_tools", timeout=self.list_timeout)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return await self.owner.request("call_tool", name, arguments, timeout=self.tool_timeout)
