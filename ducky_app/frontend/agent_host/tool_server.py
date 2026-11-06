"""The agent host serves its agents' Ducky tools itself, on a thread of this process.

Each host used to start a ``--shared-daemon`` process of its own (``shared_mcp-h<slot>.json``):
one more interpreter with every import and cache, 0.7-0.9 GB idle on Nov 5 2026. Now the
host's readiness gate (``shared_mcp.start_daemon_from_app``) calls :func:`start`, which runs
the same serve loop here (``serve_daemon(mcp, embedded=True)``) and writes that state file with
the host's own pid. Adapters connect exactly as before; each connection's hello still carries
its agent's conv id, project and host pipe, so every call runs as its own agent.

A tool call that raises answers the agent with an error (``shared_mcp._client_loop``); the host
keeps running. If another process already serves this slot (an older daemon still holding the
lock), :func:`start` leaves it to answer.

Plugin engines load here on their first tool call (``plugin_host.tool_registry``: helpers list
a plugin's tools from its cached schemas). :func:`note_deferred` remembers which plugins the
host served that way, and :func:`shed_engines` (the warm host's ``shed`` op, idle) unloads the
ones a turn loaded since and serves them as stubs again, so an idle host keeps no engine.
"""

from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
_thread: threading.Thread | None = None
READY_WAIT_S = 20.0
# plugin id -> (root, manifest) of every plugin this host served lazily at least once.
_deferred: dict[str, tuple[Any, dict[str, Any]]] = {}


def _server() -> Any:
    """The tool registry the ``--shared-daemon`` process builds, minus the app's own work
    (IDE config, skill shipping, AppData upkeep)."""
    from backend.server import mcp

    import backend.tools  # noqa: F401 - core tools before plugins

    try:
        from frontend.ui_web.workspace_bootstrap import install as install_workspace_adapters

        install_workspace_adapters()  # file history + follow-code for writes made from here
    except Exception:  # noqa: BLE001
        pass
    try:
        from backend.bridge.plugin_gate import install_bridge_plugin_gate

        install_bridge_plugin_gate(mcp)  # tools/list and calls wait for plugins, not startup
    except Exception:  # noqa: BLE001
        pass
    threading.Thread(target=_warmup, args=(mcp,), daemon=True, name="host-tool-warmup").start()
    return mcp


def _warmup(mcp: Any) -> None:
    steps = (
        ("backend.uefn_plugins.plugin_host.boot", "ensure_plugins_loaded", ()),
        ("backend.bridge.plugin_gate", "clear_mcp_tool_cache", (mcp,)),
        ("backend.bridge.dynamic_tools", "register_dynamic_listener_tools", ()),
        ("backend.mcp_plugins.bridge_proxy", "sync_nested_mcp_proxies", ()),
        ("backend.mcp_plugins.bridge_proxy", "schedule_nested_proxy_retries", ()),
    )
    import importlib

    for module, name, args in steps:
        try:
            getattr(importlib.import_module(module), name)(*args)
        except Exception:  # noqa: BLE001 - one step failing never stops the others
            pass
        if name == "ensure_plugins_loaded":
            note_deferred()


def note_deferred() -> list[str]:
    """Remember the plugins this host serves as stubs now (their engines not imported)."""
    try:
        from backend.uefn_plugins.plugin_host import tool_registry

        with tool_registry._LAZY_GUARD:
            pending = dict(tool_registry._LAZY_PENDING)
    except Exception:  # noqa: BLE001 - no plugin host (tests): nothing to remember
        return []
    with _lock:
        for pid, src in pending.items():
            _deferred.setdefault(pid, src)
        return sorted(_deferred)


def loaded_engines() -> list[str]:
    """Plugins served lazily here whose engine a tool call has loaded since."""
    try:
        from backend.uefn_plugins.plugin_host.tool_registry import lazy_pending
    except Exception:  # noqa: BLE001
        return []
    with _lock:
        pids = sorted(_deferred)
    return [pid for pid in pids if not lazy_pending(pid)]


def shed_engines() -> list[str]:
    """Unload every engine :func:`loaded_engines` names and list its tools as stubs again
    (same names and schemas); the next call loads it once more. Returns the plugins shed.

    Only plugins whose schema cache still says lazy are touched: one that must load eagerly
    (or whose copy changed on disk) stays as it is."""
    from backend.uefn_plugins.plugin_host import tool_registry
    from backend.uefn_plugins.plugin_host.lifecycle import invalidate_plugin_runtime

    shed: list[str] = []
    for pid in loaded_engines():
        with _lock:
            root, manifest = _deferred[pid]
        if tool_registry.lazy_tool_schemas(pid, root, manifest) is None:
            continue
        try:
            invalidate_plugin_runtime(pid)
            if not tool_registry.defer_plugin_register(pid, root, manifest):
                with tool_registry._LAZY_GUARD:
                    tool_registry._LAZY_PENDING.setdefault(pid, (root, manifest))
                tool_registry.load_lazy_backend(pid)  # could not stub it: load it back now
                continue
        except Exception:  # noqa: BLE001 - one plugin never stops the others
            continue
        shed.append(pid)
    if shed:
        try:
            from backend.bridge.plugin_gate import clear_mcp_tool_cache
            from backend.server import mcp

            clear_mcp_tool_cache(mcp)
        except Exception:  # noqa: BLE001
            pass
    return shed


def start(timeout_s: float = READY_WAIT_S) -> dict[str, Any]:
    """Serve this host's tools on a thread (once); returns when they answer."""
    global _thread
    from backend.bridge import shared_mcp

    with _lock:
        if _thread is not None and _thread.is_alive():
            return {"ok": True, "started": False, "in_host": True}
        mcp = _server()
        _thread = threading.Thread(target=shared_mcp.serve_daemon, args=(mcp,), kwargs={"embedded": True},
                                   daemon=True, name="host-tool-server")
        _thread.start()
        thread = _thread
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if shared_mcp.serving() and shared_mcp._ready.is_set():
            return {"ok": True, "started": True, "in_host": True}
        if not thread.is_alive():
            # Another process holds this slot's lock: it answers (the readiness gate checks).
            return {"ok": True, "started": False, "in_host": False}
        time.sleep(0.02)
    return {"ok": False, "error": "the host's tool server did not start"}


def stop(timeout_s: float = 2.0) -> None:
    """The host is exiting: stop serving and remove the state file."""
    global _thread
    from backend.bridge import shared_mcp

    with _lock:
        thread, _thread = _thread, None
    if thread is None:
        return
    shared_mcp.request_stop()
    thread.join(timeout_s)
