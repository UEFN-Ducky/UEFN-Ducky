"""First load of every enabled plugin, ``ensure_plugins_loaded``, and the missing-backend repair."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

from backend.uefn_plugins.store import appdata_uefn_plugins_dir, get_enabled_plugin_ids

_LOADED = False
# True once plugin.json UI rows are applied — Settings can paint before register().
_UI_READY = False
_LOAD_THREAD: threading.Thread | None = None
# Single-flight "scan missing backends" coordinator — never runs register() under _LOCK.
_REPAIR_THREAD: threading.Thread | None = None


def _notify_uefn_plugins_changed() -> None:
    """Tell React the Store plugin registry changed (HTTP bus — never evaluate_js)."""
    try:
        from frontend.ui_web.agent_turns.push import push_ui_event

        push_ui_event({"type": "uefn_plugins_changed"})
    except Exception:
        pass
    if _LOADED:  # a register()/unload finished after boot: enable, reload, repair, late register
        try:
            from backend.search import indexer

            indexer.request_refresh("plugins")  # debounced; re-lists the tool catalog
        except Exception:
            pass


def reload_plugins() -> None:
    # Let Store toggle workers finish so we don't invalidate mid-register().
    wait_plugin_toggles()
    # Capture who was registered / enabled before wiping contribution lists so we
    # can drop stale module objects and re-run register() (Store Update replaces
    # AppData files; keeping _REGISTERED skipped register and left detect/models broken).
    try:
        previously = set(get_enabled_plugin_ids()) | set(_REGISTERED)
    except Exception:
        previously = set(_REGISTERED)
    with _LOAD_START_LOCK:
        global _LOADED, _UI_READY, _LOAD_THREAD
        # Wait for any in-flight first load before wiping (avoid racing the worker).
        if _LOAD_THREAD is not None and _LOAD_THREAD.is_alive():
            _LOAD_DONE.wait(timeout=120.0)
        with _LOCK:
            _LOADED = False
            _UI_READY = False
            _LOAD_DONE.clear()
            _LOAD_THREAD = None
            _CONTRIBUTIONS["settings_tabs"] = []
            _CONTRIBUTIONS["settings_sections"] = []
            _CONTRIBUTIONS["dock_panels"] = []
            _CONTRIBUTIONS["editor_kinds"] = []
            _CONTRIBUTIONS["header_buttons"] = []
            _CONTRIBUTIONS["header_menus"] = []
            _CONTRIBUTIONS["ui_panels"] = []
            _CONTRIBUTIONS["shell_boots"] = []
            _CONTRIBUTIONS["appearance_profiles"] = []
            _CONTRIBUTIONS["appearance_css"] = []
            _CONTRIBUTIONS["appearance_effects"] = []
            _CONTRIBUTIONS["appearance_skins"] = []
            _CONTRIBUTIONS["sounds"] = []
            _CONTRIBUTIONS["hooks"] = []
            _CONTRIBUTIONS["automations_nodes"] = []
            _CONTRIBUTIONS["automations_triggers"] = []
            _CONTRIBUTIONS["automations_templates"] = []
            _CONTRIBUTIONS["automations_image_generators"] = []
            _CONTRIBUTIONS["automations_media_backends"] = []
            try:
                from backend.automations.plugin import clear_all as _clear_auto_handlers

                _clear_auto_handlers()
            except Exception:
                pass
            _CONTRIBUTIONS["verse_templates"] = []
            _CONTRIBUTIONS["tts_voices"] = []
            _CONTRIBUTIONS["llm_providers"] = []
            _CONTRIBUTIONS["llm_coding_agents"] = []
            _CONTRIBUTIONS["ide_hookups"] = []
            _CONTRIBUTIONS["agent_tools"] = {}
            _CONTRIBUTIONS["walkthroughs"] = []
            _PLUGIN_INTENT_ROWS.clear()
            _PLUGIN_TOOL_NAMES.clear()
            _PLUGIN_PLAN_TOOLS.clear()
            _PLUGIN_DESTRUCTIVE_TOOLS.clear()
    for pid in sorted(previously):
        try:
            invalidate_plugin_runtime(pid)
        except Exception:
            pass
    ensure_plugins_loaded()
    # Store install/enable can add Codex / etc. — drop stale CLI detect cache.
    try:
        from backend.agent.coding_agents.base import invalidate_detect_cache

        invalidate_detect_cache()
    except Exception:
        pass


def _repair_missing_backends_async() -> None:
    """Kick a single-flight background pass that retries unfinished register().

    Never blocks the caller and never runs under ``_LOCK`` — a hung plugin
    ``register()`` (e.g. unity-mcp nested MCP sync) used to freeze every Store
    enable/disable/uninstall that needed the host lock.
    """
    global _REPAIR_THREAD
    # Nearly every call finds nothing to repair. Asking first keeps get_contributions()
    # (key status, model refresh, tool lists) from starting a thread and a fresh database
    # connection on every call: ~1.3 a second while Ducky sat idle.
    try:
        if not _repair_due():
            return
    except Exception:  # noqa: BLE001 — the worker checks again
        pass
    with _REPAIR_THREAD_LOCK:
        if _REPAIR_THREAD is not None and _REPAIR_THREAD.is_alive():
            return
        _REPAIR_THREAD = threading.Thread(
            target=_repair_missing_backends_worker,
            daemon=True,
            name="uefn-plugins-repair",
        )
        _REPAIR_THREAD.start()


def _repair_due() -> bool:
    """True when an enabled plugin has not finished register() and its retry backoff passed."""
    now = time.monotonic()
    for pid in get_enabled_plugin_ids():
        if pid in _REGISTERED:
            continue
        if now - float(_REPAIR_ATTEMPTS.get(pid) or 0.0) >= _REPAIR_BACKOFF_S:
            return True
    return False


def _repair_missing_backends_worker() -> None:
    try:
        _repair_missing_backends()
    except Exception:  # noqa: BLE001 — never kill the app on repair
        _log.exception("UEFN plugin backend repair failed")


def _repair_missing_backends() -> None:
    """Re-register enabled Store plugins that contributed UI but never finished register().

    First-boot import blips used to leave llm_providers rows with no factories —
    Default Model empty + Coding Agents 'Backend not loaded' until full restart.

    Safe to call from the repair worker only — never from a thread holding ``_LOCK``.
    """
    # A plugin whose register() reaches back into get_contributions() /
    # ensure_plugins_loaded() re-enters here, and it is not in _REGISTERED yet
    # (that happens only after register returns) — so it repairs itself forever.
    # That RecursionError killed verse + vfx on every load and made every
    # ensure_plugins_loaded() call pay a failed re-import.
    if getattr(_REPAIR_STATE, "active", False):
        return
    _REPAIR_STATE.active = True
    try:
        _repair_missing_backends_once()
    finally:
        _REPAIR_STATE.active = False


def _repair_missing_backends_once() -> None:
    """Scan missing backends; kick each register() on its own daemon (not under _LOCK).

    Per-plugin backoff prevents a permanently hung ``register()`` from being
    re-kicked every sidebar poll. Each register runs off the coordinator so one
    hung plugin cannot block repair of the others.
    """
    enabled = set(get_enabled_plugin_ids())
    root = appdata_uefn_plugins_dir()
    now = time.monotonic()
    for pid in sorted(enabled):
        if pid in _REGISTERED:
            _REPAIR_ATTEMPTS.pop(pid, None)
            continue
        last = float(_REPAIR_ATTEMPTS.get(pid) or 0.0)
        if now - last < _REPAIR_BACKOFF_S:
            continue
        # Record *before* register so a hung call is not retried every poll, and before
        # the folder checks so an enabled plugin with no folder or manifest is not "due"
        # again on every call either.
        _REPAIR_ATTEMPTS[pid] = now
        child = root / pid
        if not child.is_dir():
            continue
        manifest = _read_manifest(child)
        if not manifest:
            continue

        def _repair_one(
            repair_pid: str = pid,
            repair_root: Path = child,
            repair_manifest: dict[str, Any] = manifest,
        ) -> None:
            lock = _plugin_load_lock(repair_pid)
            if not lock.acquire(blocking=False):
                return  # another load of this plugin is running: it registers it
            try:
                if repair_pid in _REGISTERED or not is_plugin_enabled(repair_pid):
                    return
                # _load_one refreshes contribs; do not wipe first or a failed register
                # leaves LLMs empty until the next app update.
                _load_enabled_plugin_locked(repair_pid, repair_root, repair_manifest)
                if repair_pid in _REGISTERED:
                    _REPAIR_ATTEMPTS.pop(repair_pid, None)
                    _notify_uefn_plugins_changed()
            except Exception:  # noqa: BLE001 — never kill repair coordinator siblings
                _log.exception("Plugin %s repair register failed", repair_pid)
            finally:
                lock.release()

        # Not a toggle thread — wait_plugin_toggles must not join hung repairs.
        threading.Thread(
            target=_repair_one,
            daemon=True,
            name=f"uefn-plugin-repair-{pid}",
        ).start()


def plugins_ready() -> bool:
    """True once the first ensure_plugins_loaded pass has finished."""
    return _LOADED


def plugins_ui_ready() -> bool:
    """True once plugin.json UI contributions are applied (backends may still register)."""
    return _UI_READY or _LOADED


def in_plugin_load_thread() -> bool:
    """True on the load thread / a register() worker — never wait on our own load."""
    return bool(getattr(_LOAD_THREAD_STATE, "in_load", False))


def wait_plugins_loaded(timeout: float | None = None) -> bool:
    """Block until the first plugin load finishes.

    Returns True when ready. If ``timeout`` is set and elapses first, returns False
    (callers may still proceed with whatever tools are already registered).
    """
    if _LOADED or in_plugin_load_thread():
        return _LOADED
    ensure_plugins_loaded_async()
    if timeout is None:
        _LOAD_DONE.wait()
        return bool(_LOADED)
    return bool(_LOAD_DONE.wait(timeout=float(timeout)))


def _flush_load_callbacks() -> None:
    with _LOAD_CALLBACKS_LOCK:
        callbacks = list(_LOAD_CALLBACKS)
        _LOAD_CALLBACKS.clear()
    for cb in callbacks:
        try:
            cb()
        except Exception:  # noqa: BLE001 — never kill plugin load on UI notify
            _log.exception("plugin load on_done callback failed")


def _boot_trace_plugins_load(duration_ms: float) -> None:
    try:
        from frontend.perf_trace import ensure_started, trace

        ensure_started()
        payload: dict[str, Any] = {}
        import os
        import time as _time

        raw = os.environ.get("UEFN_DUCKY_BOOT_T0")
        if raw:
            try:
                payload["total_ms"] = round((_time.perf_counter() - float(raw)) * 1000.0, 1)
            except ValueError:
                pass
        trace("boot", "plugins_load", duration_ms=duration_ms, **payload)
    except Exception:
        pass


def _run_first_plugin_load() -> None:
    """Import + register every enabled plugin. Caller owns single-flight.

    Two-phase so Settings can show Installed tabs before slow register() work
    (Blender addon deploy, Discord presence, gateway factories, …) finishes.
    """
    global _LOADED, _UI_READY
    # Drop compiled .pyd files a previous run parked while they were still mapped.
    try:
        sweep_compiled_quarantine()
        sweep_orphan_compiled_folders()
    except Exception:
        _log.debug("compiled quarantine sweep failed", exc_info=True)
    # Read last launch's crash markers before this process writes any: once loads run
    # in parallel, a marker of this process is a load in flight, not a crash.
    _promote_dead_markers()
    enabled = set(get_enabled_plugin_ids())
    root = appdata_uefn_plugins_dir()
    if not root.is_dir():
        with _LOCK:
            _UI_READY = True
            _LOADED = True
        return
    jobs: list[tuple[str, Path, dict[str, Any]]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        manifest = _read_manifest(child)
        if not manifest:
            continue
        pid = str(manifest.get("id") or child.name).strip().lower()
        if pid not in enabled:
            continue
        jobs.append((pid, child, manifest))
    # Phase 1 — plugin.json UI rows only (fast). Settings polls / paints Installed.
    for pid, child, manifest in jobs:
        try:
            _load_one(pid, child, manifest, register=False)
        except Exception as exc:  # noqa: BLE001 — never kill app startup
            _log.exception("UEFN plugin %s contrib load failed: %s", pid, exc)
            _record_plugin_load_error(pid, exc)
    with _LOCK:
        _UI_READY = True
    # HTTP bus only — not _flush_load_callbacks (PanelApi still needs one
    # notify after register() so LLM/coding-agent factories refresh).
    _notify_uefn_plugins_changed()
    _log.info("UEFN plugins: %d enabled (%s)", len(jobs), ", ".join(j[0] for j in jobs))
    # Parallel pre-import of source backends: exec_module dominates boot; register
    # (and every compiled backend's import) stays serial after.
    t_import = time.perf_counter()
    if len(jobs) > 1:
        from concurrent.futures import ThreadPoolExecutor

        workers = min(8, len(jobs))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="uefn-plugin-import") as pool:
            list(pool.map(lambda j: _warm_plugin_backend(j[0], j[1], j[2]), jobs))
    _log.info("UEFN plugins: import phase %.0fms", (time.perf_counter() - t_import) * 1000.0)
    # Phase 2 — register() backends (may be slow; UI already has contribs).
    t_register = time.perf_counter()
    slow = [pid for pid, child, manifest in jobs if not _register_plugin_guarded(pid, child, manifest)]
    with _LOCK:
        _UI_READY = True
        _LOADED = True
    # Pause any private plugin whose audience access is currently lost (plain-source
    # items; compiled ones also self-pause through their baked gate).
    try:
        reevaluate_private_plugins()
    except Exception:
        _log.debug("private-plugin reevaluation at boot failed", exc_info=True)
    _start_crash_heal()
    _start_files_watch()
    _log.info(
        "UEFN plugins ready: register phase %.0fms%s",
        (time.perf_counter() - t_register) * 1000.0,
        f" (still finishing: {', '.join(slow)})" if slow else "",
    )


def _plugin_load_worker() -> None:
    import time

    global _LOADED
    _LOAD_THREAD_STATE.in_load = True
    t0 = time.perf_counter()
    try:
        if not _LOADED:
            _run_first_plugin_load()
    except Exception:  # noqa: BLE001 — mark done so waiters never hang
        _log.exception("UEFN plugin background load failed")
        with _LOCK:
            _LOADED = True
    finally:
        _LOAD_DONE.set()
        _boot_trace_plugins_load((time.perf_counter() - t0) * 1000.0)
        _flush_load_callbacks()


def ensure_plugins_loaded_async(on_done: Callable[[], None] | None = None) -> None:
    """Start first plugin load on a daemon thread (no-op if already loaded/in flight)."""
    if on_done is not None:
        # One entry per function: every panel object queues its own bound ready hook, and
        # each poll during boot queued another, so plugins-ready fired ~30 app-wide
        # "plugins changed" pushes and the panel refetched 500 KB of contributions each time.
        key = getattr(on_done, "__func__", on_done)
        with _LOAD_CALLBACKS_LOCK:
            if all(getattr(cb, "__func__", cb) is not key for cb in _LOAD_CALLBACKS):
                _LOAD_CALLBACKS.append(on_done)
    if _LOADED:
        _flush_load_callbacks()
        return
    global _LOAD_THREAD
    with _LOAD_START_LOCK:
        if _LOADED:
            _flush_load_callbacks()
            return
        if _LOAD_THREAD is not None and _LOAD_THREAD.is_alive():
            return
        _LOAD_DONE.clear()
        _LOAD_THREAD = threading.Thread(
            target=_plugin_load_worker,
            daemon=True,
            name="uefn-plugins-load",
        )
        _LOAD_THREAD.start()


def ensure_plugins_loaded(timeout: float | None = None) -> bool:
    """Block until plugins are loaded (agent/MCP). Safe if async load already started.

    When ``timeout`` is set and the first load has not finished, return False without
    kicking repair — Store install/uninstall must not wait forever on a hung plugin
    ``register()`` at boot. Agent/MCP callers pass ``timeout=None`` (default) and
    still wait until ready.

    Missing-backend repair is always kicked on a background thread and never runs
    under ``_LOCK`` (a hung ``register()`` must not freeze Store toggles).

    Called from inside the load pass itself (a plugin ``register()`` that saves
    settings, reads contributions, …) it returns the current state instead of
    waiting — waiting there deadlocks the load and wedges the whole panel.
    """
    if in_plugin_load_thread():
        return _LOADED
    if not _LOADED:
        ensure_plugins_loaded_async()
        if timeout is None:
            _LOAD_DONE.wait()
        elif not _LOAD_DONE.wait(timeout=max(0.0, float(timeout))):
            return False
    if _LOADED:
        # Never hold _LOCK across register() — repair is async + per-plugin backoff.
        _repair_missing_backends_async()
        return True
    if timeout is not None:
        # Boot still in flight (or never marked ready) — caller asked not to hang.
        return False
    # Should not happen; fail-soft so callers never hang forever.
    _run_first_plugin_load()
    _LOAD_DONE.set()
    _repair_missing_backends_async()
    return True


# Names from the rest of the host, bound after every definition above so the host
# modules can import each other in any order.
from backend.uefn_plugins.plugin_host.backends import (  # noqa: E402
    sweep_compiled_quarantine,
    sweep_orphan_compiled_folders,
)
from backend.uefn_plugins.plugin_host.crash_guard import _promote_dead_markers, _start_crash_heal  # noqa: E402
from backend.uefn_plugins.plugin_host.errors import _record_plugin_load_error  # noqa: E402
from backend.uefn_plugins.plugin_host.lifecycle import (  # noqa: E402
    _start_files_watch,
    invalidate_plugin_runtime,
    is_plugin_enabled,
    reevaluate_private_plugins,
    wait_plugin_toggles,
)
from backend.uefn_plugins.plugin_host.loader import (  # noqa: E402
    _load_enabled_plugin_locked,
    _load_one,
    _plugin_load_lock,
    _read_manifest,
    _register_plugin_guarded,
    _warm_plugin_backend,
)
from backend.uefn_plugins.plugin_host.state import (  # noqa: E402
    _CONTRIBUTIONS,
    _LOAD_CALLBACKS,
    _LOAD_CALLBACKS_LOCK,
    _LOAD_DONE,
    _LOAD_START_LOCK,
    _LOAD_THREAD_STATE,
    _LOCK,
    _PLUGIN_DESTRUCTIVE_TOOLS,
    _PLUGIN_INTENT_ROWS,
    _PLUGIN_PLAN_TOOLS,
    _PLUGIN_TOOL_NAMES,
    _REGISTERED,
    _REPAIR_ATTEMPTS,
    _REPAIR_BACKOFF_S,
    _REPAIR_STATE,
    _REPAIR_THREAD_LOCK,
    _log,
)
