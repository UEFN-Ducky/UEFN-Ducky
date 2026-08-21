"""Register nested MCP server tools onto the IDE-facing FastMCP bridge.

Enabled servers from ``mcp.json`` are proxied as ``prefix__tool`` on the shared
``uefn-ducky`` server so Cursor/Claude only need one MCP connection.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Callable

from backend.server import mcp

_log = logging.getLogger("uefn_ducky.mcp_bridge_proxy")

# Tool names currently registered as nested proxies (for remove on sync).
_PROXY_TOOL_NAMES: set[str] = set()
_SYNCING = False
_FIRST_SYNC_ATTEMPTED = threading.Event()


def nested_proxy_tool_names() -> set[str]:
    """Names currently registered as nested MCP proxies on this bridge process."""
    return set(_PROXY_TOOL_NAMES)


def wait_until_nested_proxies_synced(timeout: float = 20.0) -> bool:
    """Block until the first nested-proxy sync attempt finishes (success or fail)."""
    return _FIRST_SYNC_ATTEMPTED.wait(timeout=timeout)


def _registered_tool_names() -> set[str]:
    tm = getattr(mcp, "_tool_manager", None)
    tools = getattr(tm, "_tools", None)
    return set(tools.keys()) if isinstance(tools, dict) else set()


def _clear_list_tools_cache() -> None:
    try:
        from backend.bridge.plugin_gate import clear_mcp_tool_cache

        clear_mcp_tool_cache(mcp)
    except Exception:
        pass


def _remove_proxy_tool(name: str) -> None:
    tm = getattr(mcp, "_tool_manager", None)
    if tm is None:
        return
    try:
        tm.remove_tool(name)
    except Exception:
        tools = getattr(tm, "_tools", None)
        if isinstance(tools, dict):
            tools.pop(name, None)
    _PROXY_TOOL_NAMES.discard(name)


def _make_proxy(namespaced_name: str, description: str) -> Callable[..., str]:
    def _tool(params: dict[str, Any] | None = None, pretty: bool = False) -> str:
        del pretty
        from backend.mcp_plugins.client_pool import get_plugin_pool

        async def _call() -> str:
            return await get_plugin_pool().call_tool(namespaced_name, params or {})

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            # Bridge call_tool usually runs in an event loop — nest safely.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(_call())).result(timeout=180)
        return asyncio.run(_call())

    _tool.__name__ = namespaced_name.replace("__", "_")
    _tool.__doc__ = (description or namespaced_name).strip() or namespaced_name
    return _tool


async def sync_nested_mcp_proxies_async() -> list[str]:
    """Add/remove FastMCP proxy tools for currently enabled nested servers."""
    global _SYNCING
    if _SYNCING:
        return []
    _SYNCING = True
    added: list[str] = []
    try:
        from backend.mcp_plugins.client_pool import get_plugin_pool
        from backend.mcp_plugins.store import ensure_plugin_prefix_cache

        ensure_plugin_prefix_cache()
        pool = get_plugin_pool()
        try:
            wanted_tools = await pool.list_all_plugin_tools()
        except Exception as exc:
            _log.warning("nested MCP list failed: %s", exc)
            wanted_tools = []

        wanted_names = {t.name for t in wanted_tools}
        # Remove proxies that are no longer enabled / available.
        for name in list(_PROXY_TOOL_NAMES):
            if name not in wanted_names:
                _remove_proxy_tool(name)

        existing = _registered_tool_names()
        for tool in wanted_tools:
            name = tool.name
            if name in existing and name not in _PROXY_TOOL_NAMES:
                # Collision with a real FastMCP tool — skip.
                continue
            if name in _PROXY_TOOL_NAMES:
                continue
            try:
                fn = _make_proxy(name, tool.description or name)
                mcp.tool(name=name)(fn)
                _PROXY_TOOL_NAMES.add(name)
                existing.add(name)
                added.append(name)
            except Exception as exc:
                _log.warning("failed to proxy %s: %s", name, exc)
        if added:
            _log.info("nested MCP proxies: +%d (%s)", len(added), ", ".join(added[:8]))
            _clear_list_tools_cache()
    finally:
        _SYNCING = False
        _FIRST_SYNC_ATTEMPTED.set()
    return added


def sync_nested_mcp_proxies(log: Callable[[str], None] | None = None) -> list[str]:
    """Sync proxy tools (safe at bridge start). Never raises."""
    try:
        added = asyncio.run(sync_nested_mcp_proxies_async())
        if log is not None and added:
            log(f"nested MCP proxies: +{len(added)}")
        return added
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:  # noqa: BLE001
        # An unreachable nested server tears down mid-connect and surfaces as
        # CancelledError, which is not an Exception — it used to kill this
        # daemon thread with a full traceback dump.
        _log.warning("sync_nested_mcp_proxies failed: %s: %s", type(exc).__name__, exc)
        _FIRST_SYNC_ATTEMPTED.set()
        return []


def schedule_sync_nested_proxies() -> None:
    """Best-effort resync after mcp.json changes in the panel process.

    The IDE bridge is a separate process — it picks up changes on reconnect.
    Syncing here keeps an in-process FastMCP (if any) aligned.

    Never run ``asyncio.run`` on the caller thread — unity-mcp ``register()``
    used to call this during Store enable repair and freeze Discord toggles
    while waiting on a dead Unity MCP HTTP session.
    """
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            loop.create_task(sync_nested_mcp_proxies_async())
            return
        threading.Thread(
            target=sync_nested_mcp_proxies,
            daemon=True,
            name="mcp-nested-proxy-sync",
        ).start()
    except Exception:
        pass


def schedule_nested_proxy_retries(*, delays_sec: tuple[float, ...] = (2.0, 6.0, 15.0)) -> None:
    """If Epic MCP was offline at first sync, retry until unreal__ tools appear."""

    def _worker() -> None:
        for delay in delays_sec:
            time.sleep(delay)
            try:
                from backend.mcp_plugins.epic import epic_mcp_enabled, probe_epic_mcp

                if not epic_mcp_enabled():
                    return
                if not probe_epic_mcp(ttl_sec=0).get("epic_mcp_online"):
                    continue
                if any(n.startswith("unreal__") for n in _PROXY_TOOL_NAMES):
                    return
                sync_nested_mcp_proxies()
                if any(n.startswith("unreal__") for n in _PROXY_TOOL_NAMES):
                    _log.info("nested MCP proxies: unreal tools registered after retry")
                    return
            except Exception as exc:
                _log.warning("nested proxy retry failed: %s", exc)

    threading.Thread(target=_worker, daemon=True, name="mcp-nested-proxy-retry").start()
