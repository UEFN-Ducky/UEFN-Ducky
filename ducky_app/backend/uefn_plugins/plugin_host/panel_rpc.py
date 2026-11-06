"""Calls from a plugin's panels into its backend (``plugin_call(plugin_id, name, params)``)."""

from __future__ import annotations

import time
from typing import Any


def _with_panel_note(plugin_id: str, result: Any) -> Any:
    """An agent's first call to a plugin whose panels are all off screen says so."""
    try:
        from backend.uefn_plugins.panel_presence import panel_note_once

        note = panel_note_once(plugin_id)
    except Exception:
        return result
    if not note:
        return result
    if isinstance(result, dict):
        return {**result, "panel_note": note} if "panel_note" not in result else result
    if isinstance(result, str):
        return f"{result}\n\n{note}"
    return result


def register_panel_rpc(plugin_id: str, name: str, fn: Any) -> None:
    """Register a PanelApi / bridge callable for ``plugin_call(plugin_id, name, params)``."""
    from backend.uefn_plugins.store import normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    method = str(name or "").strip()
    if not method or not callable(fn):
        raise ValueError("register_panel_rpc requires a non-empty name and callable")
    with _LOCK:
        slot = _PANEL_RPC.setdefault(pid, {})
        slot[method] = fn


def call_panel_rpc(plugin_id: str, method: str, params: dict[str, Any] | None = None) -> Any:
    """Dispatch a plugin panel RPC. Raises ValueError when disabled / unknown.

    Never calls ``ensure_plugins_loaded()`` — that path repairs every enabled
    plugin (unity-mcp nested sync) and used to freeze Discord Settings on
    ``Loading…`` for minutes.
    """
    from backend.uefn_plugins.store import load_plugin_manifest, normalize_plugin_id, plugin_dir

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError as e:
        raise ValueError(str(e)) from e
    if not is_plugin_enabled(pid):
        raise ValueError(f"Plugin '{pid}' is disabled — enable it in Settings → Store")
    from backend.uefn_plugins.plugin_host.tool_registry import lazy_pending, load_lazy_backend

    if lazy_pending(pid):  # deferred at boot: its first panel call loads it
        load_lazy_backend(pid)
    name = str(method or "").strip()
    with _LOCK:
        fn = (_PANEL_RPC.get(pid) or {}).get(name)
    if fn is None:
        # Async Store enable may still be registering this plugin — wait for it,
        # then load only this plugin (never the full repair storm).
        wait_plugin_toggles(timeout=8.0)
        with _LOCK:
            fn = (_PANEL_RPC.get(pid) or {}).get(name)
        empty = not (_PANEL_RPC.get(pid) or {})
        if fn is None and (empty or pid not in _REGISTERED):
            # Registered-but-empty happens after invalidate + reload race.
            if empty:
                with _LOCK:
                    _REGISTERED.discard(pid)
            root = plugin_dir(pid)
            manifest = load_plugin_manifest(pid)
            # A plugin that failed to load fails the same way on every call: a panel
            # polling account.status reloaded Account every 2 s. Retry on the repair backoff.
            now = time.monotonic()
            failed_lately = pid in _LOAD_FAILURES and (
                now - float(_REPAIR_ATTEMPTS.get(pid) or 0.0) < _REPAIR_BACKOFF_S
            )
            if manifest is not None and root.is_dir() and not failed_lately:
                _REPAIR_ATTEMPTS[pid] = now
                _load_enabled_plugin(pid, root, manifest)
            with _LOCK:
                fn = (_PANEL_RPC.get(pid) or {}).get(name)
    if fn is None:
        failure = _LOAD_FAILURES.get(pid)
        if failure:
            raise ValueError(f"{pid} didn't load: {failure}")
        raise ValueError(f"Unknown panel RPC: {pid}.{name}")
    kwargs = dict(params or {})
    from backend.uefn_plugins.scopes import caller, write_group

    # What the call writes to a shared copy goes out as one change, the person's (or the
    # agent's whose run made the call).
    actor, ducky = caller("user")
    with write_group(pid, actor=actor, ducky=ducky, message=name):
        # Its folders moved since it loaded here (a switch, a pull, a project change in any
        # process): it reloads first, so the panel never saves the old copy's state into the new one.
        if pid in _FOLDER_EPOCH and reload_if_folders_moved(pid):
            with _LOCK:
                fn = (_PANEL_RPC.get(pid) or {}).get(name)
            if fn is None:
                raise ValueError(f"{pid} restarted after its files changed and no longer has {name}.")
        return fn(**kwargs)


# Names from the rest of the host, bound after every definition above so the host
# modules can import each other in any order.
from backend.uefn_plugins.plugin_host.folders import reload_if_folders_moved  # noqa: E402
from backend.uefn_plugins.plugin_host.lifecycle import is_plugin_enabled, wait_plugin_toggles  # noqa: E402
from backend.uefn_plugins.plugin_host.loader import _load_enabled_plugin  # noqa: E402
from backend.uefn_plugins.plugin_host.state import (  # noqa: E402
    _FOLDER_EPOCH,
    _LOAD_FAILURES,
    _LOCK,
    _PANEL_RPC,
    _REGISTERED,
    _REPAIR_ATTEMPTS,
    _REPAIR_BACKOFF_S,
)
