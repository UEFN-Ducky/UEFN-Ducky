"""Lazy stdio MCP client pool for enabled plugins."""

from __future__ import annotations

import asyncio
import functools
import logging
import sys
import threading
import time
from collections.abc import Callable, Coroutine
from contextlib import AsyncExitStack, nullcontext
from dataclasses import dataclass, field
from typing import Any, TypeVar

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Tool

from backend.mcp_plugins.registry import namespace_tool_name
from backend.mcp_plugins.session_owner import OwnedSession, SessionOwner
from backend.mcp_plugins.store import (
    effective_plugin_ids,
    ensure_plugin_prefix_cache,
    load_plugin_manifest,
    resolve_server_block,
)

_CONNECT_TIMEOUT_SEC = 45.0
_HTTP_CONNECT_TIMEOUT_SEC = 2.0
_HTTP_FAIL_CACHE_SEC = 10.0
_TOOL_TIMEOUT_SEC = 180.0
_IDLE_EVICT_SEC = 15 * 60.0
_POOL_RUN_TIMEOUT_SEC = 180.0
_TOGGLE_HINT = (
    "toggle this MCP (Settings → MCPs, or ducky_mcp_set_plugin) when the task is done."
)


def _http_transport_kwargs(block: dict[str, Any]) -> dict[str, Any]:
    """Epic PlaceDevice / execute_tool_script regularly outlive the SDK 30s default."""
    return {
        "headers": block.get("headers") or None,
        "timeout": _TOOL_TIMEOUT_SEC,
        "sse_read_timeout": _TOOL_TIMEOUT_SEC,
    }

_T = TypeVar("_T")
log = logging.getLogger(__name__)


from backend.agent.mcp_content import mcp_content_to_text as _content_to_text


def _pool_loop_bound(fn: Callable[..., Coroutine[Any, Any, _T]]) -> Callable[..., Coroutine[Any, Any, _T]]:
    """Run the method on this pool's owned loop when `own_loop=True`."""

    @functools.wraps(fn)
    async def wrapped(self: PluginClientPool, *args: Any, **kwargs: Any) -> _T:
        if self._closed:
            raise RuntimeError("plugin pool is closed")
        if not self._own_loop:
            return await fn(self, *args, **kwargs)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            return await fn(self, *args, **kwargs)
        coro = fn(self, *args, **kwargs)
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, self._loop))

    return wrapped


@dataclass
class PluginConnection:
    plugin_id: str
    manifest: dict[str, Any]
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    owner: SessionOwner | None = None
    session: OwnedSession | None = None
    last_used: float = 0.0
    retired: bool = False


class PluginClientPool:
    def __init__(self, *, own_loop: bool = False) -> None:
        self._connections: dict[str, PluginConnection] = {}
        self._tools_cache: list[Tool] | None = None
        self._tools_cache_ids: tuple[str, ...] | None = None
        # namespaced tool name -> its server's annotations (readOnlyHint etc.).
        # Lets the change journal tell a nested read from a nested mutation.
        self._tool_annotations: dict[str, Any] = {}
        self._failed_until: dict[str, float] = {}
        self._own_loop = own_loop
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._loop_ready = threading.Event()
        self._pool_lock: asyncio.Lock | None = None
        self._closing = False
        self._closed = False
        # Also retain owners whose cleanup timed out, until they actually exit.
        self._owners: set[SessionOwner] = set()
        if own_loop:
            self._start_owned_loop()
        else:
            self._pool_lock = asyncio.Lock()

    def _start_owned_loop(self) -> None:
        def _runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._pool_lock = asyncio.Lock()
            self._loop_ready.set()
            try:
                loop.run_forever()
            finally:
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.run_until_complete(loop.shutdown_default_executor())
                loop.close()

        self._thread = threading.Thread(target=_runner, name="mcp-plugin-pool", daemon=True)
        self._thread.start()
        if not self._loop_ready.wait(timeout=5) or self._pool_lock is None:
            raise RuntimeError("plugin pool loop failed to start")

    def run_sync(self, coro: Coroutine[Any, Any, _T], timeout: float = _POOL_RUN_TIMEOUT_SEC) -> _T:
        """Run a pool coroutine from any thread. Never uses asyncio.run on a live loop."""
        if self._closed:
            coro.close()
            raise RuntimeError("plugin pool is closed")
        if self._own_loop and self._loop is not None:
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is self._loop:
                coro.close()
                raise RuntimeError("cannot run_sync from the plugin pool loop")
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            try:
                return future.result(timeout=timeout)
            except TimeoutError:
                future.cancel()
                raise
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        coro.close()
        raise RuntimeError("run_sync from a running loop requires own_loop=True")

    async def run_async(self, coro: Coroutine[Any, Any, _T]) -> _T:
        if self._closed:
            coro.close()
            raise RuntimeError("plugin pool is closed")
        if not self._own_loop or self._loop is None:
            return await coro
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            return await coro
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, self._loop))

    def invalidate_tools_cache(self) -> None:
        self._tools_cache = None
        self._tools_cache_ids = None
        self._tool_annotations = {}

    def annotations_for(self, namespaced_name: str) -> Any | None:
        """A nested tool's MCP annotations, or None if unknown or not yet listed."""
        return self._tool_annotations.get(namespaced_name)

    async def _get_or_create(self, plugin_id: str) -> PluginConnection:
        async with self._pool_lock:
            if self._closing:
                raise RuntimeError("plugin pool is shutting down")
            conn = self._connections.get(plugin_id)
            if conn is None:
                manifest = load_plugin_manifest(plugin_id)
                if not manifest:
                    raise FileNotFoundError(f"MCP plugin not found: {plugin_id}")
                conn = PluginConnection(plugin_id=plugin_id, manifest=manifest)
                self._connections[plugin_id] = conn
            return conn

    def _http_skip_until(self, plugin_id: str) -> float:
        return float(self._failed_until.get(plugin_id) or 0)

    def _mark_http_fail(self, plugin_id: str) -> None:
        self._failed_until[plugin_id] = time.time() + _HTTP_FAIL_CACHE_SEC
        self.invalidate_tools_cache()

    def _clear_http_fail(self, plugin_id: str) -> None:
        self._failed_until.pop(plugin_id, None)

    async def _ensure_session(self, conn: PluginConnection) -> OwnedSession:
        async with conn.lock:
            if self._closing or conn.retired:
                raise RuntimeError("plugin pool is shutting down")
            if conn.session is not None and conn.owner is not None and conn.owner.alive:
                conn.last_used = time.time()
                return conn.session
            await self._close_connection_unlocked(conn)
            skip_until = self._http_skip_until(conn.plugin_id)
            if skip_until and time.time() < skip_until:
                raise ConnectionError(
                    f"MCP HTTP {conn.plugin_id} recently unreachable; retry after "
                    f"{max(0.0, skip_until - time.time()):.0f}s"
                )
            ttype = ""
            try:
                block = resolve_server_block(conn.manifest)
                ttype = str(block.get("type") or "")
                init_timeout = _CONNECT_TIMEOUT_SEC
                if ttype in ("http", "sse"):
                    from backend.mcp_plugins.epic import tcp_probe_url

                    url = str(block.get("url") or "")
                    if not await asyncio.to_thread(tcp_probe_url, url):
                        self._mark_http_fail(conn.plugin_id)
                        raise ConnectionError(f"MCP HTTP unreachable: {url}")
                    init_timeout = _HTTP_CONNECT_TIMEOUT_SEC
                if self._closing or conn.retired:
                    raise ConnectionError(f"Nested MCP '{conn.plugin_id}' connection closed")

                async def open_session(stack: AsyncExitStack) -> ClientSession:
                    if ttype == "http":
                        from mcp.client.streamable_http import streamablehttp_client

                        transport = await stack.enter_async_context(
                            streamablehttp_client(block["url"], **_http_transport_kwargs(block))
                        )
                    elif ttype == "sse":
                        from mcp.client.sse import sse_client

                        transport = await stack.enter_async_context(
                            sse_client(block["url"], **_http_transport_kwargs(block))
                        )
                    else:
                        params = StdioServerParameters(
                            command=block["command"], args=block["args"], env=block.get("env") or None,
                        )
                        transport = await stack.enter_async_context(stdio_client(params))
                    session = await stack.enter_async_context(ClientSession(transport[0], transport[1]))
                    # This scope nests INSIDE the transport/session scopes and
                    # exits before them. All context entry/exit stays in the owner.
                    with anyio.fail_after(init_timeout):
                        await session.initialize()
                    return session

                owner = SessionOwner(conn.plugin_id, open_session)
                conn.owner = owner
                self._owners.add(owner)
                owner._task.add_done_callback(lambda task: self._owners.discard(owner))
                await owner.start()
                conn.session = OwnedSession(owner, list_timeout=_CONNECT_TIMEOUT_SEC, tool_timeout=_TOOL_TIMEOUT_SEC)
                conn.last_used = time.time()
                self._clear_http_fail(conn.plugin_id)
                return conn.session
            except BaseException as exc:
                # The caller can be under an AnyIO cancelled scope. Shield the
                # wait; cleanup itself runs in the original owner task.
                with anyio.CancelScope(shield=True):
                    await self._close_connection_unlocked(conn)
                if isinstance(exc, Exception) and ttype in ("http", "sse"):
                    self._mark_http_fail(conn.plugin_id)
                raise

    async def _close_connection_unlocked(self, conn: PluginConnection) -> None:
        conn.session = None
        if conn.owner is not None:
            await conn.owner.close()
            conn.owner = None

    async def _close_connection(self, conn: PluginConnection) -> None:
        # An initializer holds conn.lock while awaiting the handshake. Stop its
        # owner first so disabling/shutdown does not wait for that handshake.
        if conn.owner is not None:
            await conn.owner.close()
        async with conn.lock:
            await self._close_connection_unlocked(conn)

    def close_plugin(self, plugin_id: str) -> None:
        if self._closed:
            return
        self.invalidate_tools_cache()

        async def _do_close() -> None:
            conn = self._connections.pop(plugin_id, None)
            if conn is None:
                return
            conn.retired = True
            await self._close_connection(conn)

        if self._own_loop and self._loop is not None:
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is self._loop:
                running.create_task(_do_close())
                return
            self.run_sync(_do_close(), timeout=30)
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_do_close())
        except RuntimeError:
            asyncio.run(_do_close())

    def shutdown_sync(self) -> None:
        """Close every nested session, then stop the owned loop if any."""
        if self._closed:
            return
        if self._own_loop and self._loop is not None:
            self.run_sync(self.shutdown_all(), timeout=30)
            # Do not abandon live owners on a stopped loop if cleanup failed.
            loop = self._loop
            self._closed = True
            loop.call_soon_threadsafe(loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=5)
            self._loop = None
            self._thread = None
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.shutdown_all())
            self._closed = True
            return
        raise RuntimeError("shutdown_sync from a running loop requires own_loop=True")

    @_pool_loop_bound
    async def shutdown_all(self) -> None:
        self.invalidate_tools_cache()
        async with self._pool_lock:
            self._closing = True
            connections = list(self._connections.values())
            for conn in connections:
                conn.retired = True
        results = await asyncio.gather(*(self._close_connection(conn) for conn in connections), return_exceptions=True)
        results += await asyncio.gather(*(owner.close() for owner in list(self._owners)), return_exceptions=True)
        errors = [result for result in results if isinstance(result, Exception)]
        if errors:
            raise ExceptionGroup("Nested MCP cleanup failed", errors)
        self._connections.clear()

    @_pool_loop_bound
    async def evict_idle(self, max_idle_sec: float = _IDLE_EVICT_SEC) -> int:
        """Close plugin connections unused for longer than ``max_idle_sec``."""
        now = time.time()
        async with self._pool_lock:
            stale = [
                pid
                for pid, conn in self._connections.items()
                if conn.session is not None and (now - conn.last_used) > max_idle_sec
                and (conn.owner is None or not conn.owner.busy)
            ]
        for pid in stale:
            conn = self._connections.pop(pid, None)
            if conn:
                conn.retired = True
                await self._close_connection(conn)
        if stale:
            self.invalidate_tools_cache()
        return len(stale)

    @_pool_loop_bound
    async def list_tools_for_plugin(self, plugin_id: str) -> list[Tool]:
        conn = await self._get_or_create(plugin_id)
        session = await self._ensure_session(conn)
        try:
            result = await asyncio.wait_for(session.list_tools(), timeout=_CONNECT_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            raise
        except Exception:
            # Same self-heal as call_tool: a dead pooled stream would otherwise
            # hide this plugin's tools from the registry until app restart.
            await self._close_connection(conn)
            session = await self._ensure_session(conn)
            result = await asyncio.wait_for(session.list_tools(), timeout=_CONNECT_TIMEOUT_SEC)
        tools = list(result.tools or [])
        namespaced: list[Tool] = []
        for tool in tools:
            name = namespace_tool_name(conn.manifest, tool.name)
            # Keep annotations and outputSchema: readOnlyHint/destructiveHint are how
            # the change journal knows whether a nested call mutated the project.
            annotations = getattr(tool, "annotations", None)
            if annotations is not None:
                self._tool_annotations[name] = annotations
            namespaced.append(
                Tool(
                    name=name,
                    description=tool.description or tool.name,
                    inputSchema=tool.inputSchema,
                    annotations=annotations,
                    outputSchema=getattr(tool, "outputSchema", None),
                )
            )
        return namespaced

    @_pool_loop_bound
    async def list_all_plugin_tools(self) -> list[Tool]:
        ensure_plugin_prefix_cache()
        ids = tuple(effective_plugin_ids())
        if self._tools_cache is not None and self._tools_cache_ids == ids:
            return list(self._tools_cache)
        out: list[Tool] = []
        failed = False
        for pid in ids:
            try:
                out.extend(await self.list_tools_for_plugin(pid))
            except Exception:
                failed = True
                continue
        if not failed:
            self._tools_cache = out
            self._tools_cache_ids = ids
        return list(out)

    @_pool_loop_bound
    async def call_tool(self, namespaced_name: str, arguments: dict[str, Any] | None) -> str:
        from backend.mcp_plugins.registry import parse_plugin_tool

        ensure_plugin_prefix_cache()
        parsed = parse_plugin_tool(namespaced_name)
        if not parsed:
            raise ValueError(f"Not a plugin tool: {namespaced_name}")
        plugin_id, original_name = parsed
        args = arguments or {}
        # Empty-str exceptions (bare TimeoutError/ConnectionError) surface to agents as
        # "Error executing tool X:" with no detail — always raise with a real message.
        try:
            conn = await self._get_or_create(plugin_id)
            session = await self._ensure_session(conn)
        except Exception as e:
            detail = str(e).strip() or type(e).__name__
            raise RuntimeError(
                f"Nested MCP '{plugin_id}' session unavailable for tool "
                f"'{original_name}': {detail}. The server may answer TCP but have a "
                f"dead MCP session — use the Ducky fallback tool for this step; {_TOGGLE_HINT}"
            ) from e
        def _watchdog():
            # Epic's MCP runs on UEFN's Slate thread: a Save prompt it opens would
            # hold this call for the whole timeout, so the host presses it meanwhile.
            from backend.mcp_plugins.epic import EPIC_MCP_PREFIX

            if plugin_id != EPIC_MCP_PREFIX:
                return nullcontext()
            from backend.tools.core.uefn_modal import save_modal_watchdog

            return save_modal_watchdog(f"{plugin_id}:{original_name}")

        try:
            with _watchdog():
                raw = await asyncio.wait_for(
                    session.call_tool(original_name, args), timeout=_TOOL_TIMEOUT_SEC
                )
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"Nested MCP '{plugin_id}' tool '{original_name}' timed out after "
                f"{_TOOL_TIMEOUT_SEC:.0f}s — server reachable but unresponsive. Use "
                f"the Ducky fallback tool for this step; {_TOGGLE_HINT}"
            ) from None
        except Exception:
            # Dead pooled stream (ClosedResourceError etc.): the server restarted
            # since we cached this session, while status probes open fresh sockets
            # and still say "online". Self-heal — drop the session, reconnect,
            # retry ONCE — instead of failing every call until app restart.
            try:
                await self._close_connection(conn)
                session = await self._ensure_session(conn)
                with _watchdog():
                    raw = await asyncio.wait_for(
                        session.call_tool(original_name, args), timeout=_TOOL_TIMEOUT_SEC
                    )
                self.invalidate_tools_cache()  # server restart may have changed tools
            except asyncio.TimeoutError:
                raise RuntimeError(
                    f"Nested MCP '{plugin_id}' tool '{original_name}' timed out after "
                    f"{_TOOL_TIMEOUT_SEC:.0f}s on a fresh session. Use the Ducky "
                    f"fallback tool for this step; {_TOGGLE_HINT}"
                ) from None
            except Exception as e2:
                detail = str(e2).strip() or type(e2).__name__
                raise RuntimeError(
                    f"Nested MCP '{plugin_id}' tool '{original_name}' failed even "
                    f"after an automatic reconnect: {detail}. Use the Ducky fallback "
                    f"tool for this step; {_TOGGLE_HINT}"
                ) from e2
        conn.last_used = time.time()
        if hasattr(raw, "content"):
            return _content_to_text(raw.content)
        return _content_to_text(raw)

    @_pool_loop_bound
    async def test_plugin(self, plugin_id: str) -> dict[str, Any]:
        manifest = load_plugin_manifest(plugin_id)
        if not manifest:
            return {"ok": False, "error": f"Plugin not found: {plugin_id}"}
        ensure_plugin_prefix_cache()
        try:
            transport = resolve_server_block(manifest).get("type", "stdio")
        except Exception:
            transport = "stdio"
        stages: list[dict[str, Any]] = []
        try:
            tools = await self.list_tools_for_plugin(plugin_id)
            stages.append({"stage": "list_tools", "ok": True, "tool_count": len(tools)})
        except Exception as e:
            msg = str(e)
            hint = _hint_for_connect_error(msg, transport)
            return {"ok": False, "error": msg, "hint": hint, "stages": stages}

        probe = manifest.get("health_probe_tool")
        if isinstance(probe, str) and probe.strip():
            probe_name = namespace_tool_name(manifest, probe.strip())
            try:
                await self.call_tool(probe_name, {})
                stages.append({"stage": "health_probe", "ok": True, "tool": probe_name})
            except Exception as e:
                stages.append({"stage": "health_probe", "ok": False, "error": str(e)})
                return {
                    "ok": False,
                    "error": str(e),
                    "hint": "MCP server started but health probe failed — check external app (e.g. Blender addon).",
                    "stages": stages,
                    "tool_count": len(tools),
                }
        return {"ok": True, "tool_count": len(tools), "stages": stages, "tools": [t.name for t in tools[:40]]}


def _hint_for_connect_error(msg: str, transport: str = "stdio") -> str:
    low = msg.lower()
    if transport in ("http", "sse"):
        return _hint_for_http_error(low)
    return _hint_for_spawn_error(low)


def _hint_for_http_error(low: str) -> str:
    if "401" in low or "403" in low or "unauthorized" in low or "forbidden" in low:
        return (
            "Auth rejected — check the plugin's Authorization header / API token "
            "(use ${SECRET:NAME} and store the token in the keyring)."
        )
    if "404" in low or "not found" in low:
        return "Endpoint returned 404 — verify the server.url path (e.g. it should end in /mcp)."
    if "timed out" in low or "timeout" in low:
        return "Connection timed out — the MCP endpoint is unreachable or slow; verify the URL and that the server is running."
    if "getaddrinfo" in low or "name or service" in low or "nodename" in low or "resolve" in low:
        return "DNS lookup failed — the host in server.url can't be resolved; check for typos."
    if "refused" in low or "connect call failed" in low or "connection error" in low:
        return "Connection refused — nothing is listening at server.url; check host/port and that the server is up."
    if "certificate" in low or "ssl" in low or "tls" in low:
        return "TLS/certificate error — the endpoint's certificate is invalid or self-signed."
    if "sse" in low or "text/event-stream" in low or "content-type" in low:
        return "Transport mismatch — the server may expect a different type (try 'sse' vs 'streamable-http')."
    return "HTTP MCP connection failed — verify server.url, transport type, and auth headers."


def _hint_for_spawn_error(low: str) -> str:
    if "enoent" in low or "not found" in low:
        if sys.platform == "win32":
            return "Install uv and use full path to uvx, or cmd /c uvx in plugin server config."
        return "Install uv (https://docs.astral.sh/uv/) so uvx is on PATH."
    if "connection" in low or "9876" in low:
        return "Start the external app addon (e.g. Blender MCP sidebar → Connect)."
    return ""


_pool: PluginClientPool | None = None
_singleton_lock = threading.Lock()


def get_plugin_pool() -> PluginClientPool:
    global _pool
    with _singleton_lock:
        if _pool is None:
            _pool = PluginClientPool(own_loop=True)
        return _pool


def shutdown_plugin_pool() -> None:
    """Stop an existing pool without creating a new thread during app shutdown."""
    with _singleton_lock:
        pool = _pool
    if pool is not None:
        pool.shutdown_sync()
