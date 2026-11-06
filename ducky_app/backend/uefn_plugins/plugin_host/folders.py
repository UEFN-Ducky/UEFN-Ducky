"""Plugins that keep declared folders (team_folders): bind them before a load, and reload the
plugin when another process moved them (a switch, a pull, a project change)."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from backend.process_role import is_app


def _folder_plugin_call(pid: str, tool_name: str, current: Callable[..., Any]) -> Callable[..., Any] | None:
    """Before a tool call of a plugin that keeps declared folders: an agent's call first
    lands the team's changes waiting behind Update now (an agent never edits a stale
    base); then, when its folders moved since this process loaded it, it reloads here and
    the call goes to the reloaded tool, which is returned. None: run the tool as loaded."""
    from backend.uefn_plugins.scopes import caller

    actor, _ = caller("user" if is_app() else "agent")
    if actor == "agent":
        try:
            from backend.uefn_plugins import vcs_engine

            vcs_engine.apply_waiting(pid)
        except Exception:  # noqa: BLE001 — the call still runs; Update now stays on the bar
            _log.debug("landing %s's waiting changes before a call failed", pid, exc_info=True)
    if not reload_if_folders_moved(pid):
        return None
    from backend.server import mcp

    fn = getattr(mcp._tool_manager.get_tool(tool_name), "fn", None)
    if not callable(fn) or fn is current:
        raise RuntimeError(f"{pid} restarted after its files changed and no longer has the tool {tool_name}.")
    return fn


def _bind_folders(pid: str) -> int | None:
    """A plugin that keeps declared folders, about to load: they point at the copy it reads
    now (:func:`team_folders.bind_plugin`). Returns the reload epoch it loads at (None: it
    keeps none). A bind that fails never stops the load; the next one tries again."""
    try:
        from backend.uefn_plugins import team_folders

        if not team_folders.plugin_folders(pid):
            return None
    except Exception:  # noqa: BLE001 — a manifest that can't be read: no folders
        return None
    try:
        out = team_folders.bind_plugin(pid)
        if not out.get("ok"):
            _log.warning("Plugin %s: its folders couldn't be pointed at its copy: %s", pid, out.get("error"))
    except Exception:  # noqa: BLE001
        _log.warning("Plugin %s: binding its folders failed", pid, exc_info=True)
    try:
        from backend.store.repos import folder_files

        return folder_files.reload_epoch(pid)
    except Exception:  # noqa: BLE001 — without an epoch it is never reloaded for a move
        _log.debug("Plugin %s: no reload epoch", pid, exc_info=True)
        return None


def bind_folders_after_engine() -> None:
    """The team-sync engine arrived (Ducky Account registered): plugins that loaded before
    it get their declared folders bound now, and a reload epoch to follow moves by."""
    for pid in list(_LOADED_SIGNATURE):
        if pid in _FOLDER_EPOCH:
            continue
        epoch = _bind_folders(pid)
        if epoch is not None:
            _FOLDER_EPOCH[pid] = epoch


def _folder_reload_lock(pid: str) -> threading.Lock:
    with _FOLDER_RELOAD_GUARD:
        return _FOLDER_RELOAD_LOCKS.setdefault(pid, threading.Lock())


def _folders_moved(pid: str) -> bool:
    """The plugin's folders moved (its reload epoch) since this process loaded it."""
    loaded = _FOLDER_EPOCH.get(pid)
    if loaded is None:
        return False
    from backend.store.repos import folder_files

    return folder_files.reload_epoch(pid) != loaded


def reload_if_folders_moved(pid: str) -> bool:
    """Before a tool call here: when the plugin's folders moved since this process loaded
    it, unload and load it again now (its caches belong to the old files). Returns
    whether its tools were registered anew (the call must run on the new ones). Raises
    with the reason when it can't load again."""
    if not _folders_moved(pid):
        return False
    with _folder_reload_lock(pid):
        if pid in _FOLDER_EPOCH and not _folders_moved(pid):
            return True  # another call reloaded it meanwhile
        from backend.uefn_plugins.store import load_plugin_manifest, plugin_dir

        label = pid
        try:
            invalidate_plugin_runtime(pid)
            manifest = load_plugin_manifest(pid)
            root = plugin_dir(pid)
            if manifest is None or not root.is_dir() or not is_plugin_enabled(pid):
                raise RuntimeError("it is no longer installed and on")
            label = str(manifest.get("label") or pid)
            with loading_now(pid):  # the call waiting on it needs the real tool, not a stub
                _load_one(pid, root, manifest)
            if pid not in _REGISTERED:
                raise RuntimeError(_LOAD_FAILURES.get(pid) or "its register() didn't finish")
        except Exception as exc:  # noqa: BLE001 — the call says why instead of running stale code
            _record_plugin_load_error(pid, exc)
            raise RuntimeError(f"{label} couldn't restart after its files changed: {exc}") from exc
    _log.info("Plugin %s reloaded here: its folders moved", pid)
    return True


def reload_moved_plugins(now: float | None = None) -> list[str]:
    """The app process (each scheduler tick): a plugin loaded here whose folders moved (a
    pull, a switch or a project change made in any process) restarts once its epoch has
    stayed moved for :data:`FOLDER_RELOAD_GRACE_S` (a switch restarts it itself first)
    and none of its calls is running in any process; its panels then reload. Returns the
    plugins restarting."""
    if not _FOLDER_EPOCH:
        return []
    from backend.store.repos import folder_files
    from backend.uefn_plugins import team_folders

    now = time.time() if now is None else now
    out: list[str] = []
    for pid, loaded in list(_FOLDER_EPOCH.items()):
        epoch = folder_files.reload_epoch(pid)
        if epoch == loaded:
            _FOLDER_MOVED.pop(pid, None)
            continue
        seen = _FOLDER_MOVED.get(pid)
        if seen is None or seen[0] != epoch:
            _FOLDER_MOVED[pid] = (epoch, now)
            continue
        if now - seen[1] < FOLDER_RELOAD_GRACE_S or team_folders.live_calls(pid):
            continue
        _FOLDER_MOVED.pop(pid, None)
        _FOLDER_EPOCH.pop(pid, None)  # one restart; the load records the epoch again
        out.append(pid)
        threading.Thread(target=_restart_moved, args=(pid,), name=f"uefn-plugin-folders-moved-{pid}",
                         daemon=True).start()
    return out


def _restart_moved(pid: str) -> None:
    # Live, a teammate's pull restarts only the backend: the panels keep what is open and
    # merge it like any sync (backend.uefn_plugins.live); they reload once Live ends.
    live_pull = False
    try:
        from backend.store.repos import folder_files
        from backend.uefn_plugins import live

        live_pull = folder_files.reload_reason(pid) == "pull" and live.keeps_panels(pid)
    except Exception:  # noqa: BLE001 — a full restart
        _log.debug("live check of %s failed", pid, exc_info=True)
    try:
        reload_single_plugin(pid)
    except Exception:  # noqa: BLE001 — its next tool call reloads it
        _log.warning("Plugin %s: restarting after its folders moved failed", pid, exc_info=True)
    try:
        from frontend.ui_web.agent_turns.push import push_ui_event

        if live_pull:
            from backend.uefn_plugins import live

            live.panels_kept(pid)
            push_ui_event({"type": "plugin_scope_changed", "plugins": [pid], "synced": True, "live": True})
        else:
            push_ui_event({"type": "plugin_scope_changed", "plugins": [pid], "switched": True})
    except Exception:  # noqa: BLE001 — its panels read the new files on their next open
        _log.debug("telling %s's panels about its restart failed", pid, exc_info=True)


# Names from the rest of the host, bound after every definition above so the host
# modules can import each other in any order.
from backend.uefn_plugins.plugin_host.errors import _record_plugin_load_error  # noqa: E402
from backend.uefn_plugins.plugin_host.lifecycle import (  # noqa: E402
    invalidate_plugin_runtime,
    is_plugin_enabled,
    reload_single_plugin,
)
from backend.uefn_plugins.plugin_host.loader import _load_one  # noqa: E402
from backend.uefn_plugins.plugin_host.tool_registry import loading_now  # noqa: E402
from backend.uefn_plugins.plugin_host.state import (  # noqa: E402
    _FOLDER_EPOCH,
    _FOLDER_MOVED,
    _FOLDER_RELOAD_GUARD,
    _FOLDER_RELOAD_LOCKS,
    _LOAD_FAILURES,
    _LOADED_SIGNATURE,
    _REGISTERED,
    FOLDER_RELOAD_GRACE_S,
    _log,
)
