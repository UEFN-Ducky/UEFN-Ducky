"""Crash guard: a plugin version whose load took Ducky down is skipped next launch, and
updated as soon as the Store has a newer one.

A plugin whose load takes the whole process down (a compiled backend crashing in native
code) would otherwise do it again on every launch, and Ducky could never open. Each load
in the window leaves a marker named after its process; a marker whose process is gone
means that load crashed Ducky, and that plugin version is skipped until it updates or the
user turns it on again.

Only the window (:func:`backend.process_role.is_app`) leaves markers. A tool server or an
agent host loads the same plugins and is ended by whoever started it at any moment (a chat
that stops, a coding agent closing its tools), most often right after a plugin update, when
every such process loads the new version itself. Until Nov 12 2026 one ended mid-load made
the window skip the plugin: "UEFN Ducky closed while loading Ducky Account 1.1.82" on PCs
where nothing had crashed. Records from then are not trusted: they are dropped once, so a
plugin skipped for no reason loads again (one that really crashes is found again on the
next launch).
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from backend.process_role import is_app

_LOAD_MARKERS = ".loading"
_CRASHED_FILE = "crashed-app.json"  # made from the window's own markers only
_OLD_CRASHED_FILE = "crashed.json"  # also counted tool servers ended mid-load: dropped, never read
_APP_ROLE = "app"
_crash_lock = threading.Lock()
_crash_checked = False


def _markers_dir() -> Path:
    from backend.uefn_plugins.store import appdata_uefn_plugins_dir

    return appdata_uefn_plugins_dir() / _LOAD_MARKERS


def _process_alive(os_pid: int) -> bool:
    try:
        from frontend.open_files import panel_process_alive

        return panel_process_alive(os_pid)
    except Exception:
        return True  # unknown: never blame a plugin on a guess


def _read_crashed() -> dict[str, str]:
    try:
        data = json.loads((_markers_dir() / _CRASHED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _write_crashed(crashed: dict[str, str]) -> None:
    path = _markers_dir() / _CRASHED_FILE
    try:
        if not crashed:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(crashed), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        _log.debug("could not save plugin crash record", exc_info=True)


def _promote_dead_markers() -> dict[str, str]:
    """Turn markers left by a process that died mid-load into crash records (once per process)."""
    global _crash_checked
    with _crash_lock:
        crashed = _read_crashed()
        if _crash_checked:
            return crashed
        _crash_checked = True
        folder = _markers_dir()
        if not folder.is_dir():
            return crashed
        try:
            (folder / _OLD_CRASHED_FILE).unlink(missing_ok=True)
        except OSError:
            pass
        changed = False
        for marker in folder.glob("*.json"):
            if marker.name == _CRASHED_FILE:
                continue
            try:
                info = json.loads(marker.read_text(encoding="utf-8"))
                owner = int(info.get("process") or 0)
            except (OSError, ValueError, TypeError, AttributeError):
                owner, info = 0, {}
            if owner and owner != os.getpid() and _process_alive(owner):
                continue  # another live window is loading it now
            # A marker with no role is a leftover of a version where tool servers left them too.
            plugin = str(info.get("plugin") or "") if info.get("role") == _APP_ROLE else ""
            if plugin:
                crashed[plugin] = str(info.get("version") or "")
                changed = True
                _log.warning("Plugin %s %s was loading when Ducky closed; skipping it", plugin, crashed[plugin])
            try:
                marker.unlink()
            except OSError:
                pass
        if changed:
            _write_crashed(crashed)
        return crashed


def plugin_crashed_ducky(pid: str, version: str) -> bool:
    """True when this exact plugin version took Ducky down while loading last time."""
    crashed = _promote_dead_markers()
    return pid in crashed and crashed[pid] == version


def clear_plugin_crash(pid: str) -> None:
    """The user turned the plugin on again (or it updated): let it load next time."""
    with _crash_lock:
        crashed = _read_crashed()
        if crashed.pop(pid, None) is not None:
            _write_crashed(crashed)


def heal_crashed_plugins() -> list[str]:
    """Update each plugin skipped for crashing Ducky when the Store has a newer version.

    The person can't be expected to find the Update button: the plugin that broke may be
    the one they sign in with, and the fix is usually already on the Store. The update
    installs and loads in this session (the Store's update path reloads the plugin).
    Returns the plugin ids that were updated.
    """
    crashed = _read_crashed()
    if not crashed:
        return []
    from backend.uefn_plugins.plugin_version import plugin_version_newer
    from backend.uefn_plugins.store import load_plugin_manifest

    still_skipped: dict[str, str] = {}
    for pid, bad in crashed.items():
        installed = str((load_plugin_manifest(pid) or {}).get("version") or "")
        if installed and installed == bad:
            still_skipped[pid] = bad
        else:
            clear_plugin_crash(pid)  # updated or removed since: the record is stale
    if not still_skipped:
        return []
    try:
        from frontend.duckyos_account import store_catalog, store_download_and_install

        catalog = store_catalog()
    except Exception:
        _log.debug("crashed-plugin update: Store catalog unavailable", exc_info=True)
        return []
    latest = {
        str(item.get("slug") or ""): str(item.get("latest_version") or "")
        for item in catalog.get("items") or []
        if isinstance(item, dict)
    }
    healed: list[str] = []
    for pid, bad in still_skipped.items():
        newer = latest.get(pid, "")
        if not newer or not plugin_version_newer(newer, bad):
            continue
        try:
            result = store_download_and_install(pid, replace=True, is_update=True)
        except Exception:
            _log.warning("Could not update %s after it crashed Ducky", pid, exc_info=True)
            continue
        if not (isinstance(result, dict) and result.get("ok")):
            _log.warning("Could not update %s after it crashed Ducky: %s", pid, (result or {}).get("error"))
            continue
        clear_plugin_crash(pid)
        healed.append(pid)
        _log.warning("Plugin %s %s crashed Ducky; updated it to %s", pid, bad, newer)
        try:
            from frontend.error_log import record_activity

            record_activity("plugins", f"{pid} {bad} closed Ducky while loading; updated it to {newer}")
        except Exception:
            pass
    if healed:
        _notify_uefn_plugins_changed()
    return healed


def _start_crash_heal() -> None:
    """Fetch fixed versions of crashed plugins off the boot path (the app window owns it)."""
    if not is_app() or not _read_crashed():
        return
    threading.Thread(target=heal_crashed_plugins, name="uefn-plugin-crash-heal", daemon=True).start()


def _load_marker_begin(pid: str, version: str) -> Path | None:
    if not is_app():
        return None  # a tool server or agent host ending mid-load is not Ducky closing
    path = _markers_dir() / f"{pid}.{os.getpid()}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"plugin": pid, "version": version, "process": os.getpid(), "role": _APP_ROLE}),
                        encoding="utf-8")
        return path
    except OSError:
        return None


def _load_marker_end(path: Path | None) -> None:
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


# Names from the rest of the host, bound after every definition above so the host
# modules can import each other in any order.
from backend.uefn_plugins.plugin_host.boot import _notify_uefn_plugins_changed  # noqa: E402
from backend.uefn_plugins.plugin_host.state import _log  # noqa: E402
