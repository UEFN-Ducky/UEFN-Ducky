"""Store enable / disable / reload of one plugin, unloading its runtime, private-plugin pauses."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from backend.process_role import is_app
from backend.uefn_plugins.store import (
    PLUGIN_MANIFEST,
    appdata_uefn_plugins_dir,
    get_enabled_plugin_ids,
)


def is_plugin_enabled(plugin_id: str) -> bool:
    from backend.uefn_plugins.store import normalize_plugin_id

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError:
        return False
    return pid in get_enabled_plugin_ids() and (appdata_uefn_plugins_dir() / pid / PLUGIN_MANIFEST).is_file()


def invalidate_plugin_runtime(plugin_id: str, *, unload_timeout: float = 5.0) -> None:
    """Drop register()-side runtime for a plugin so the next load re-runs register().

    Call before Store install/update replaces AppData files. Keeps TTS/LLM factories
    from pointing at stale module objects after a zip overwrite, and removes the
    plugin's MCP tools so a reinstall never keeps serving the old tool code.

    ``unload()`` runs on a helper thread and is joined with ``unload_timeout`` so a
    misbehaving plugin can never hang the Store bridge / uninstall path.
    """
    from backend.uefn_plugins.store import normalize_plugin_id

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError:
        return
    mod_name = backend_module_name(pid)
    try:  # a shared-spaces provider stops providing when its plugin unloads
        from backend.uefn_plugins import spaces

        spaces.unregister(pid)
    except Exception:  # noqa: BLE001
        _log.debug("spaces unregister failed for %s", pid, exc_info=True)
    # Let the plugin stop background threads before modules are dropped.
    mod = sys.modules.get(mod_name)
    if mod is not None:
        unload = getattr(mod, "unload", None)
        if callable(unload):
            err: list[BaseException] = []

            def _run_unload() -> None:
                try:
                    unload()
                except BaseException as exc:  # noqa: BLE001 — capture for log below
                    err.append(exc)

            t = threading.Thread(
                target=_run_unload,
                daemon=True,
                name=f"uefn-plugin-unload-{pid}",
            )
            t.start()
            t.join(timeout=max(0.0, float(unload_timeout)))
            if t.is_alive():
                _log.warning(
                    "Plugin %s unload() still running after %.1fs — continuing teardown",
                    pid,
                    float(unload_timeout),
                )
            elif err:
                _log.error("Plugin %s unload() failed: %s", pid, err[0], exc_info=err[0])
    # Wait for a load of this plugin on another thread to finish: dropping its modules
    # mid-import failed that load with KeyError 'uefn_plugin_<id>'.
    load_lock = _backend_load_lock(pid)
    held = load_lock.acquire(timeout=_INVALIDATE_WAIT_S)
    if not held:
        _log.warning("Plugin %s: still loading after %ss; unloading anyway", pid, _INVALIDATE_WAIT_S)
    try:
        _drop_plugin_runtime(pid, mod_name)
    finally:
        if held:
            load_lock.release()


_INVALIDATE_WAIT_S = 30.0


def _drop_plugin_runtime(pid: str, mod_name: str) -> None:
    from backend.uefn_plugins.plugin_host.tool_registry import _LAZY_GUARD, _LAZY_PENDING

    with _LAZY_GUARD:  # a deferred plugin's stubs go below with its other tools
        _LAZY_PENDING.pop(pid, None)
    with _LOCK:
        _REGISTERED.discard(pid)
        _LOADED_SIGNATURE.pop(pid, None)
        _FOLDER_EPOCH.pop(pid, None)
        _FOLDER_MOVED.pop(pid, None)
        _clear_node_handlers(pid)
        _TTS_SYNTHESIZERS.pop(pid, None)
        _TTS_VOICE_LISTERS.pop(pid, None)
        for key in [k for k in _MEDIA_GENERATORS if k[1] == pid]:
            _MEDIA_GENERATORS.pop(key, None)
        for key in [k for k, v in _LLM_PROVIDER_FACTORIES.items() if v.get("plugin_id") == pid]:
            _LLM_PROVIDER_FACTORIES.pop(key, None)
        for key in [k for k, v in _CODING_AGENT_FACTORIES.items() if v.get("plugin_id") == pid]:
            _CODING_AGENT_FACTORIES.pop(key, None)
        for key in [k for k, v in _IDE_HOOKUPS.items() if v.get("plugin_id") == pid]:
            _IDE_HOOKUPS.pop(key, None)
        for key in [k for k, v in _SECRET_TESTERS.items() if v.get("plugin_id") == pid]:
            _SECRET_TESTERS.pop(key, None)
        _PANEL_RPC.pop(pid, None)
        _CONNECTION_PROBES.pop(pid, None)
        _CONNECTION_CACHE.pop(pid, None)
        _PEOPLE_PROVIDERS.pop(pid, None)
        _PEOPLE_CACHE.pop(pid, None)
        from backend.uefn_plugins.scopes import forget_change_listeners

        forget_change_listeners(pid)
        try:
            from backend.uefn_plugins import live

            live.forget(pid, keep_live=is_plugin_enabled(pid))  # a reload stays live; a disable leaves
        except Exception:  # noqa: BLE001 — nothing live stays for an unloaded plugin's callbacks
            _log.debug("live forget of %s failed", pid, exc_info=True)
        # Drop the plugin's MCP tools from the shared FastMCP server too, or a reinstall
        # keeps serving the old wrapper (which still calls the dropped module's code).
        tool_names = set(_API_TOOL_REGISTRY.pop(pid, set()) or set())
        tool_names.update(n for n, owner in _PLUGIN_TOOL_OWNER.items() if owner == pid)
        _unregister_plugin_mcp_tools(tool_names)
        _API_INTENT_PATTERNS.pop(pid, None)
        _PLUGIN_TOOL_NAMES.difference_update(tool_names)
        _PLUGIN_HOST_ONLY_TOOLS.difference_update(tool_names)
        _PLUGIN_PLAN_TOOLS.difference_update(tool_names)
        _PLUGIN_DESTRUCTIVE_TOOLS.difference_update(tool_names)
        _PLUGIN_INTENT_ROWS[:] = [row for row in _PLUGIN_INTENT_ROWS if row[0] != pid]
        for name in list(tool_names):
            _PLUGIN_TOOL_OWNER.pop(name, None)
        if mod_name not in _COMPILED_LOADED:  # a compiled backend can't be re-executed
            for key in list(sys.modules):
                if key == mod_name or key.startswith(mod_name + "."):
                    del sys.modules[key]


def _spawn_toggle_thread(target: Callable[[], None], *, name: str) -> None:
    t = threading.Thread(target=target, daemon=True, name=name)
    with _TOGGLE_THREADS_LOCK:
        _TOGGLE_THREADS[:] = [x for x in _TOGGLE_THREADS if x.is_alive()]
        _TOGGLE_THREADS.append(t)
    t.start()


def wait_plugin_toggles(timeout: float = 30.0) -> None:
    """Join background Store enable/disable workers (reload / tests)."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        with _TOGGLE_THREADS_LOCK:
            alive = [t for t in _TOGGLE_THREADS if t.is_alive()]
        if not alive:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        alive[0].join(timeout=min(0.05, remaining))


def _kick_gateway_models() -> None:
    """Re-probe agents and refresh catalogs after register(). Safe if the UI is mid-warm."""
    try:
        from backend.agent.coding_agents.base import invalidate_detect_cache, kick_detect_refresh

        invalidate_detect_cache()
        kick_detect_refresh()
    except Exception:
        pass
    try:
        from frontend.ui_web.panel_api import kick_model_refresh

        kick_model_refresh()
    except Exception:
        pass


def apply_plugin_enabled_change(plugin_id: str, *, enabled: bool) -> None:
    """Fast Store enable/disable: touch only this plugin (no full reload).

    Returns quickly so the pywebview/MCP bridge never hangs on Discord
    ``register()`` / ``unload()`` or a stuck first-boot ``ensure_plugins_loaded()``.

    - Disable: strip UI contribs immediately; ``unload()`` / runtime drop on a daemon.
    - Enable: apply plugin.json UI rows immediately; ``register()`` on a daemon.
    Settings saves ``enabled_uefn_plugins`` *before* this runs, so a restart still
    restores the toggle even if background register is slow.
    """
    from backend.uefn_plugins.store import load_plugin_manifest, normalize_plugin_id, plugin_dir

    pid = normalize_plugin_id(plugin_id)
    # Kick boot if needed, but never wait — Store toggles must not block the UI thread.
    if not plugins_ready() and not plugins_ui_ready():
        ensure_plugins_loaded_async()

    if not enabled:
        with _LOCK:
            _strip_contributions_for(pid)

        def _disable_bg() -> None:
            try:
                # Re-enabled while we were queued — leave runtime alone.
                if is_plugin_enabled(pid):
                    return
                invalidate_plugin_runtime(pid)
                try:
                    from frontend.deploy import overlay_plugin_listeners_to_appdata

                    overlay_plugin_listeners_to_appdata(reload=True)
                except Exception:
                    _log.debug("Plugin %s listener overlay on disable failed", pid, exc_info=True)
            except Exception:
                _log.exception("Plugin %s disable unload failed", pid)
            finally:
                if not is_plugin_enabled(pid):
                    _notify_uefn_plugins_changed()

        _spawn_toggle_thread(_disable_bg, name=f"uefn-plugin-disable-{pid}")
        return

    root = plugin_dir(pid)
    manifest = load_plugin_manifest(pid)
    if manifest is None or not root.is_dir():
        return
    # Fast path: Settings tab / dock / header from plugin.json (no backend import).
    try:
        _load_one(pid, root, manifest, register=False)
    except Exception as exc:  # noqa: BLE001 — never kill Store toggle
        _log.exception("UEFN plugin %s contrib load failed: %s", pid, exc)
        _record_plugin_load_error(pid, exc)
    # Paint LLMs / picker now — do not wait for register() or an app restart.
    _notify_uefn_plugins_changed()

    def _enable_bg() -> None:
        try:
            if not is_plugin_enabled(pid):
                return
            _load_enabled_plugin(pid, root, manifest)
            try:
                from frontend.deploy import overlay_plugin_listeners_to_appdata

                overlay_plugin_listeners_to_appdata(reload=True)
            except Exception:
                _log.debug("Plugin %s listener overlay on enable failed", pid, exc_info=True)
            _kick_gateway_models()
        finally:
            # Enable raced with disable — drop whatever register() just attached.
            if not is_plugin_enabled(pid):
                try:
                    invalidate_plugin_runtime(pid)
                except Exception:
                    _log.exception("Plugin %s enable-race cleanup failed", pid)
                with _LOCK:
                    _strip_contributions_for(pid)
            _notify_uefn_plugins_changed()

    _spawn_toggle_thread(_enable_bg, name=f"uefn-plugin-enable-{pid}")


def reload_single_plugin(plugin_id: str) -> None:
    """Reload one plugin after Store install/update — never full reload_plugins().

    Full reload re-registers every plugin and races concurrent updates into
    duplicate header buttons / settings tabs. Single-plugin path is enough for
    zip overwrite + register().

    Waits are bounded so a wedged first-boot load cannot freeze install/uninstall
    (or hold ``_STORE_INSTALL_LOCK`` / host ``_LOCK`` indefinitely).

    ``register()`` runs on a toggle daemon — a hung plugin backend must not stick
    the Store install bridge on "Refreshing store…". Folders the plugin keeps itself
    (``team_folders``) are bound again before that register() (:func:`_bind_folders`).
    """
    from backend.uefn_plugins.store import load_plugin_manifest, normalize_plugin_id, plugin_dir

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError:
        return
    wait_plugin_toggles(timeout=5.0)
    # If boot load is still running, skip repair and only touch this plugin —
    # the loader will honor final enabled settings when it finishes.
    ensure_plugins_loaded(timeout=10.0)
    invalidate_plugin_runtime(pid)
    root = plugin_dir(pid)
    manifest = load_plugin_manifest(pid)
    if manifest is None or not root.is_dir() or pid not in set(get_enabled_plugin_ids()):
        with _LOCK:
            _strip_contributions_for(pid)
        return
    with _LOCK:
        _strip_contributions_for(pid)
    # Fast path: UI contribs from plugin.json so install can paint before register().
    lock = _plugin_load_lock(pid)
    # Bounded like the waits above: a register() hung in another load must not stick the install.
    got = lock.acquire(timeout=10.0)
    try:
        if got:
            _load_one(pid, root, manifest, register=False)
    except Exception as exc:  # noqa: BLE001 — never kill Store install
        _log.exception("UEFN plugin %s contrib reload failed: %s", pid, exc)
        _record_plugin_load_error(pid, exc)
    finally:
        if got:
            lock.release()
    # Store install/update: picker + LLMs see the gateway before register() finishes.
    _notify_uefn_plugins_changed()

    def _reload_bg() -> None:
        try:
            if not is_plugin_enabled(pid):
                return
            _load_enabled_plugin(pid, root, manifest)
            try:
                from frontend.deploy import overlay_plugin_listeners_to_appdata

                overlay_plugin_listeners_to_appdata(reload=True)
            except Exception:
                _log.debug("Plugin %s listener overlay on reload failed", pid, exc_info=True)
            _kick_gateway_models()
        finally:
            if not is_plugin_enabled(pid):
                try:
                    invalidate_plugin_runtime(pid)
                except Exception:
                    _log.exception("Plugin %s reload-race cleanup failed", pid)
                with _LOCK:
                    _strip_contributions_for(pid)
            _notify_uefn_plugins_changed()

    _spawn_toggle_thread(_reload_bg, name=f"uefn-plugin-reload-{pid}")


def _manifest_signature(root: Path) -> int:
    """An install writes plugin.json again: its mtime names the copy on disk (0: none)."""
    try:
        return (root / "plugin.json").stat().st_mtime_ns
    except OSError:
        return 0


def reload_plugins_changed_on_disk() -> list[str]:
    """The agents' tool server: a plugin it loaded that another process installed again
    (a Store update or reinstall in the app) or removed loads its new files here, so agents
    see its new tools and its old code stops running. An install still writing is waited
    out. Returns the plugins reloading.

    Oct 23 2026: the app updated Web Browser to 1.0.34 and the tool server kept the 1.0.33
    tools until the plugin was turned off and on (browser_video_frames never appeared)."""
    from backend.uefn_plugins.store import PLUGIN_INSTALL_LOCK, plugin_dir
    from backend.util.file_lock import held_file_lock

    stale = [pid for pid, sig in list(_LOADED_SIGNATURE.items()) if _manifest_signature(plugin_dir(pid)) != sig]
    if not stale:
        return []
    with held_file_lock(PLUGIN_INSTALL_LOCK, 0.0) as free:
        if not free:
            return []  # an install is still writing: the next look reloads
    for pid in stale:
        _LOADED_SIGNATURE.pop(pid, None)  # one reload; its register() records the new files
        try:
            reload_single_plugin(pid)
        except Exception:  # noqa: BLE001 — the others still reload
            _log.warning("Plugin %s: reloading its new files failed", pid, exc_info=True)
    return stale


_FILES_WATCH_STARTED = False


def _start_files_watch() -> None:
    """Only the agents' tool server: the app reloads plugins itself on its own installs, and
    an install in the tool server asks the app to reload (store._reload_in_app_process)."""
    global _FILES_WATCH_STARTED
    if is_app() or _FILES_WATCH_STARTED:
        return
    _FILES_WATCH_STARTED = True

    def _watch() -> None:
        while True:
            time.sleep(PLUGIN_FILES_POLL_S)
            try:
                reload_plugins_changed_on_disk()
            except Exception:  # noqa: BLE001 — the next look tries again
                _log.debug("plugin files check failed", exc_info=True)

    threading.Thread(target=_watch, name="uefn-plugin-files-watch", daemon=True).start()


# plugin_id -> audience it is paused for (access lost). Cleared when access returns.
_PAUSED_PRIVATE_PLUGINS: dict[str, str] = {}


def _plugin_audience(manifest: dict[str, Any]) -> str:
    """The audience recorded for an installed private plugin ('' for a public one)."""
    from backend.uefn_plugins.signing import AUDIENCE_FIELD

    vis = str(manifest.get("visibility") or "").strip()
    if vis in ("", "public"):
        return ""
    return str(manifest.get("audience") or manifest.get(AUDIENCE_FIELD) or "").strip()


def _audience_access_state(audience: str) -> tuple[bool, str]:
    """(has_access, label) for a private plugin's audience (a shared space)."""
    try:
        from backend.uefn_plugins.scopes import space_access

        return space_access(audience)
    except Exception:
        return True, audience  # never pause on an evaluation error


def reevaluate_private_plugins(account: str | None = None) -> dict[str, Any]:
    """Pause private plugins whose audience access was lost; resume them when it returns.

    Called at boot and on enable, and by the spaces provider when a space flips to
    ``lost`` or unlocks. A paused plugin is unloaded and shows "Paused, no access to
    <label>"; when access returns it is loaded again. Compiled private plugins also
    enforce this at load through the baked license gate; this handles the live
    transition and plain-source private plugins.
    """
    from backend.uefn_plugins.store import load_plugin_manifest

    paused: list[str] = []
    resumed: list[str] = []
    for pid in list(get_enabled_plugin_ids()):
        manifest = load_plugin_manifest(pid)
        if not manifest:
            continue
        audience = _plugin_audience(manifest)
        if not audience:
            continue
        has_access, label = _audience_access_state(audience)
        if not has_access:
            if pid in _REGISTERED or pid not in _PAUSED_PRIVATE_PLUGINS:
                try:
                    invalidate_plugin_runtime(pid)
                    with _LOCK:
                        _strip_contributions_for(pid)
                    _record_plugin_load_error(pid, RuntimeError(f"Paused: no access to {label}"))
                except Exception:
                    _log.exception("pausing private plugin %s failed", pid)
                _PAUSED_PRIVATE_PLUGINS[pid] = audience
                paused.append(pid)
        elif pid in _PAUSED_PRIVATE_PLUGINS:
            _PAUSED_PRIVATE_PLUGINS.pop(pid, None)
            try:
                reload_single_plugin(pid)
            except Exception:
                _log.exception("resuming private plugin %s failed", pid)
            resumed.append(pid)
    if paused or resumed:
        _notify_uefn_plugins_changed()
    return {"paused": paused, "resumed": resumed}


def is_private_plugin_paused(plugin_id: str) -> bool:
    """True when a private plugin is currently paused for lost access (Settings badge)."""
    return plugin_id in _PAUSED_PRIVATE_PLUGINS


# Names from the rest of the host, bound after every definition above so the host
# modules can import each other in any order.
from backend.uefn_plugins.plugin_host.boot import (  # noqa: E402
    _notify_uefn_plugins_changed,
    ensure_plugins_loaded,
    ensure_plugins_loaded_async,
    plugins_ready,
    plugins_ui_ready,
)
from backend.uefn_plugins.plugin_host.contributions import (  # noqa: E402
    _clear_node_handlers,
    _strip_contributions_for,
)
from backend.uefn_plugins.plugin_host.errors import _record_plugin_load_error  # noqa: E402
from backend.uefn_plugins.plugin_host.loader import (  # noqa: E402
    _backend_load_lock,
    _load_enabled_plugin,
    _load_one,
    _plugin_load_lock,
    backend_module_name,
)
from backend.uefn_plugins.plugin_host.state import (  # noqa: E402
    _API_INTENT_PATTERNS,
    _API_TOOL_REGISTRY,
    _CODING_AGENT_FACTORIES,
    _COMPILED_LOADED,
    _CONNECTION_CACHE,
    _CONNECTION_PROBES,
    _FOLDER_EPOCH,
    _FOLDER_MOVED,
    _IDE_HOOKUPS,
    _LLM_PROVIDER_FACTORIES,
    _LOADED_SIGNATURE,
    _LOCK,
    _MEDIA_GENERATORS,
    _PANEL_RPC,
    _PEOPLE_CACHE,
    _PEOPLE_PROVIDERS,
    _PLUGIN_DESTRUCTIVE_TOOLS,
    _PLUGIN_HOST_ONLY_TOOLS,
    _PLUGIN_INTENT_ROWS,
    _PLUGIN_PLAN_TOOLS,
    _PLUGIN_TOOL_NAMES,
    _PLUGIN_TOOL_OWNER,
    _REGISTERED,
    _SECRET_TESTERS,
    _TOGGLE_THREADS,
    _TOGGLE_THREADS_LOCK,
    _TTS_SYNTHESIZERS,
    _TTS_VOICE_LISTERS,
    PLUGIN_FILES_POLL_S,
    _log,
)
from backend.uefn_plugins.plugin_host.tool_registry import _unregister_plugin_mcp_tools  # noqa: E402
