"""Lazy stdio MCP client pool for enabled plugins."""

from __future__ import annotations

import asyncio
import sys
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Tool

from backend.mcp_plugins.registry import namespace_tool_name
from backend.mcp_plugins.store import (
    effective_plugin_ids,
    ensure_plugin_prefix_cache,
    load_plugin_manifest,
    resolve_server_block,
)

_CONNECT_TIMEOUT_SEC = 45.0
_TOOL_TIMEOUT_SEC = 180.0
_IDLE_EVICT_SEC = 15 * 60.0


from backend.mcp_content import mcp_content_to_text as _content_to_text


@dataclass
class PluginConnection:
    plugin_id: str
    manifest: dict[str, Any]
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    stack: AsyncExitStack | None = None
    session: ClientSession | None = None
    last_used: float = 0.0


class PluginClientPool:
    def __init__(self) -> None:
        self._connections: dict[str, PluginConnection] = {}
        self._pool_lock = asyncio.Lock()
        self._tools_cache: list[Tool] | None = None
        self._tools_cache_ids: tuple[str, ...] | None = None

    def invalidate_tools_cache(self) -> None:
        self._tools_cache = None
        self._tools_cache_ids = None

    async def _get_or_create(self, plugin_id: str) -> PluginConnection:
        async with self._pool_lock:
            conn = self._connections.get(plugin_id)
            if conn is None:
                manifest = load_plugin_manifest(plugin_id)
                if not manifest:
                    raise FileNotFoundError(f"MCP plugin not found: {plugin_id}")
                conn = PluginConnection(plugin_id=plugin_id, manifest=manifest)
                self._connections[plugin_id] = conn
            return conn

    async def _ensure_session(self, conn: PluginConnection) -> ClientSession:
        async with conn.lock:
            if conn.session is not None:
                conn.last_used = time.time()
                return conn.session
            try:
                block = resolve_server_block(conn.manifest)
                stack = AsyncExitStack()
                ttype = block["type"]
                if ttype == "http":
                    from mcp.client.streamable_http import streamablehttp_client

                    transport = await stack.enter_async_context(
                        streamablehttp_client(block["url"], headers=block.get("headers") or None)
                    )
                elif ttype == "sse":
                    from mcp.client.sse import sse_client

                    transport = await stack.enter_async_context(
                        sse_client(block["url"], headers=block.get("headers") or None)
                    )
                else:
                    params = StdioServerParameters(
                        command=block["command"],
                        args=block["args"],
                        env=block.get("env") or None,
                    )
                    transport = await stack.enter_async_context(stdio_client(params))
                # stdio/sse yield (read, write); streamable-http yields a third
                # get_session_id callback we don't need — index instead of unpack.
                read, write = transport[0], transport[1]
                session = await stack.enter_async_context(ClientSession(read, write))
                await asyncio.wait_for(session.initialize(), timeout=_CONNECT_TIMEOUT_SEC)
                conn.stack = stack
                conn.session = session
                conn.last_used = time.time()
                return session
            except Exception:
                await self._close_connection(conn)
                raise

    async def _close_connection(self, conn: PluginConnection) -> None:
        async with conn.lock:
            conn.session = None
            if conn.stack is not None:
                try:
                    await conn.stack.aclose()
                except Exception:
                    pass
                conn.stack = None

    def close_plugin(self, plugin_id: str) -> None:
        self.invalidate_tools_cache()
        conn = self._connections.pop(plugin_id, None)
        if conn is None:
            return

        async def _do_close() -> None:
            await self._close_connection(conn)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_do_close())
        except RuntimeError:
            pass

    async def shutdown_all(self) -> None:
        self.invalidate_tools_cache()
        async with self._pool_lock:
            ids = list(self._connections.keys())
        for pid in ids:
            conn = self._connections.pop(pid, None)
            if conn:
                await self._close_connection(conn)

    async def evict_idle(self, max_idle_sec: float = _IDLE_EVICT_SEC) -> int:
        """Close plugin connections unused for longer than ``max_idle_sec``."""
        now = time.time()
        async with self._pool_lock:
            stale = [
                pid
                for pid, conn in self._connections.items()
                if conn.session is not None and (now - conn.last_used) > max_idle_sec
            ]
        for pid in stale:
            conn = self._connections.pop(pid, None)
            if conn:
                await self._close_connection(conn)
        if stale:
            self.invalidate_tools_cache()
        return len(stale)

    async def list_tools_for_plugin(self, plugin_id: str) -> list[Tool]:
        conn = await self._get_or_create(plugin_id)
        session = await self._ensure_session(conn)
        result = await asyncio.wait_for(session.list_tools(), timeout=_CONNECT_TIMEOUT_SEC)
        tools = list(result.tools or [])
        namespaced: list[Tool] = []
        for tool in tools:
            namespaced.append(
                Tool(
                    name=namespace_tool_name(conn.manifest, tool.name),
                    description=tool.description or tool.name,
                    inputSchema=tool.inputSchema,
                )
            )
        return namespaced

    async def list_all_plugin_tools(self) -> list[Tool]:
        ensure_plugin_prefix_cache()
        ids = tuple(effective_plugin_ids())
        if self._tools_cache is not None and self._tools_cache_ids == ids:
            return list(self._tools_cache)
        out: list[Tool] = []
        for pid in ids:
            try:
                out.extend(await self.list_tools_for_plugin(pid))
            except Exception:
                continue
        self._tools_cache = out
        self._tools_cache_ids = ids
        return list(out)

    async def call_tool(self, namespaced_name: str, arguments: dict[str, Any] | None) -> str:
        from backend.mcp_plugins.registry import parse_plugin_tool

        ensure_plugin_prefix_cache()
        parsed = parse_plugin_tool(namespaced_name)
        if not parsed:
            raise ValueError(f"Not a plugin tool: {namespaced_name}")
        plugin_id, original_name = parsed
        conn = await self._get_or_create(plugin_id)
        session = await self._ensure_session(conn)
        args = arguments or {}
        raw = await asyncio.wait_for(session.call_tool(original_name, args), timeout=_TOOL_TIMEOUT_SEC)
        conn.last_used = time.time()
        if hasattr(raw, "content"):
            return _content_to_text(raw.content)
        return _content_to_text(raw)

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


def get_plugin_pool() -> PluginClientPool:
    global _pool
    if _pool is None:
        _pool = PluginClientPool()
    return _pool
