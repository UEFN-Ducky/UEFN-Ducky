"""Phase 1 importers: settings, secrets, recent projects, workspace state,
models cache, plugin cache and prefs.

Each import runs once per database (``meta.imported:<store>``), reads the
legacy file(s) under the AppData root, writes rows, verifies what it wrote and
then moves the source into ``legacy/<store>/`` — except the settings file,
which stays in place as the shadow copy ``PanelSettings.save()`` keeps writing
during this release (rollback: ``DUCKY_STORE_BACKEND=files``).
"""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from typing import Any, Callable

from backend.store import db
from backend.store.repos import kv

_lock = threading.Lock()
LEGACY_DIR = "legacy"


def _flag(name: str) -> str:
    return f"imported:{name}"


_done: set[tuple[str, str]] = set()  # (db path, store) seen imported in this process


def once(name: str, fn: Callable[[Path], dict[str, Any]]) -> dict[str, Any] | None:
    """Run *fn(app_root)* once per database. Returns its report, or None when done before."""
    memo = (str(db.db_path()), name)
    if memo in _done:
        return None
    with _lock:
        if memo in _done:
            return None
        if kv.meta_get(_flag(name)) is not None:
            _done.add(memo)
            return None
        root = db.app_root()
        report = fn(root)
        kv.meta_set(_flag(name), json.dumps(report, ensure_ascii=False))
        _done.add(memo)
        return report


def reset_for_tests() -> None:
    with _lock:
        _done.clear()


def _move_to_legacy(root: Path, source: Path, store: str) -> None:
    if not source.exists():
        return
    dest_dir = root / LEGACY_DIR / store
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / source.name
    if dest.exists():
        if dest.is_dir():
            shutil.rmtree(dest, ignore_errors=True)
        else:
            dest.unlink(missing_ok=True)
    try:
        source.replace(dest)
    except OSError:
        if source.is_dir():
            shutil.copytree(source, dest, dirs_exist_ok=True)
            shutil.rmtree(source, ignore_errors=True)
        else:
            shutil.copy2(source, dest)
            source.unlink(missing_ok=True)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- settings


def import_settings(root: Path) -> dict[str, Any]:
    from frontend.settings import PanelSettings

    path = root / "panel_settings.json"
    data = _read_json(path)
    if not isinstance(data, dict):
        return {"source": str(path), "found": False}
    from backend.agent.secrets import reject_api_key_fields
    from backend.store.repos import settings as repo

    data = reject_api_key_fields(data)
    known = {f.name for f in PanelSettings.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    values = {k: v for k, v in data.items() if k in known and v is not None}
    defaults = PanelSettings().to_json_dict()
    repo.replace_all(values, defaults)
    stored = repo.load_fields()
    non_default = {k: v for k, v in values.items() if defaults.get(k) != v}
    assert stored == non_default, "settings import verification failed"
    # The file stays: PanelSettings.save() shadows every save into it this release.
    return {"source": str(path), "found": True, "fields": len(non_default)}


# --------------------------------------------------------------------------- secrets


def import_secrets(root: Path) -> dict[str, Any]:
    from backend.agent import secrets as legacy
    from backend.store.repos import secrets as repo

    path = root / "credentials.dat"
    if not path.is_file() or not legacy._dpapi_available():
        return {"source": str(path), "found": False}
    try:
        keys = legacy._deserialize_keys(path.read_bytes())
    except (OSError, ValueError, json.JSONDecodeError):
        return {"source": str(path), "found": True, "error": "unreadable; left in place"}
    repo.replace_all({name: legacy.protect_text(value) for name, value in keys.items()})
    back = {name: legacy.unprotect_text(blob) for name, blob in repo.all_blobs().items()}
    assert back == keys, "secrets import verification failed"
    _move_to_legacy(root, path, "secrets")
    return {"source": str(path), "found": True, "names": sorted(keys)}


# --------------------------------------------------------------------------- projects


def import_recent_projects(root: Path) -> dict[str, Any]:
    from backend.store.repos import projects as repo

    path = root / "recent_projects.json"
    data = _read_json(path)
    raw = data.get("projects") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return {"source": str(path), "found": False}
    paths: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip() and item not in paths:
            paths.append(item.strip())
    repo.replace_recent(paths[: repo.MAX_RECENT])
    assert repo.recent_paths() == paths[: repo.MAX_RECENT], "recent projects import verification failed"
    _move_to_legacy(root, path, "projects")
    return {"source": str(path), "found": True, "count": len(paths)}


# --------------------------------------------------------------------------- workspace state


def import_workspace_state(root: Path) -> dict[str, Any]:
    report: dict[str, Any] = {"bounds": 0, "dock": 0, "editor": 0}
    bounds = _read_json(root / "window_bounds.json")
    if isinstance(bounds, dict):
        for key, value in bounds.items():
            if isinstance(key, str) and isinstance(value, dict):
                kv.set_doc("workspace_state", f"bounds:{key}", value)
                report["bounds"] += 1
        _move_to_legacy(root, root / "window_bounds.json", "workspace_state")
    dock = _read_json(root / "workspace_dock.json")
    if isinstance(dock, dict):
        for key, value in dock.items():
            if isinstance(key, str) and isinstance(value, dict):
                kv.set_doc("workspace_state", f"dock:{key}", value)
                report["dock"] += 1
        _move_to_legacy(root, root / "workspace_dock.json", "workspace_state")
    ws_root = root / "workspace" / "projects"
    if ws_root.is_dir():
        for child in sorted(ws_root.iterdir()):
            doc = _read_json(child / "editor.json")
            if child.is_dir() and isinstance(doc, dict):
                kv.set_doc("workspace_state", f"editor:{child.name}", doc)
                report["editor"] += 1
        _move_to_legacy(root, root / "workspace", "workspace_state")
    return report


# --------------------------------------------------------------------------- cache docs


def import_cache_docs(root: Path) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for name, key in (("models_cache.json", "models_cache"), ("mcp_command_manifest.json", "mcp_command_manifest")):
        doc = _read_json(root / name)
        if isinstance(doc, dict):
            kv.set_doc("cache_docs", key, doc)
            report[key] = True
            if key != "models_cache":  # models_cache.json stays as the shadow copy this release
                _move_to_legacy(root, root / name, "cache_docs")
    return report


# --------------------------------------------------------------------------- plugin kv


def import_plugin_kv(root: Path) -> dict[str, Any]:
    from backend.store.repos import plugin_kv as repo

    report: dict[str, Any] = {"cache_docs": 0, "prefs": 0}
    cache_root = root / "uefn_plugin_cache"
    if cache_root.is_dir():
        for plugin_dir in sorted(cache_root.iterdir()):
            if not plugin_dir.is_dir():
                continue
            for file in sorted(plugin_dir.glob("*.json")):
                doc = _read_json(file)
                if isinstance(doc, dict):
                    repo.set(plugin_dir.name, file.stem, doc)
                    report["cache_docs"] += 1
        _move_to_legacy(root, cache_root, "plugin_kv")
    prefs = _read_json(root / "uefn_plugin_prefs" / "all.json")
    if isinstance(prefs, dict):
        for pid, slot in prefs.items():
            if isinstance(pid, str) and isinstance(slot, dict):
                repo.set_prefs(pid, slot)
                report["prefs"] += 1
        _move_to_legacy(root, root / "uefn_plugin_prefs", "plugin_kv")
    return report


ALL = {
    "settings": import_settings,
    "secrets": import_secrets,
    "projects": import_recent_projects,
    "workspace_state": import_workspace_state,
    "cache_docs": import_cache_docs,
    "plugin_kv": import_plugin_kv,
}


def ensure(name: str) -> None:
    """Run the named importer once. Cheap after the first call (one meta lookup)."""
    once(name, ALL[name])


def ensure_all() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in ALL:
        report = ensure_report(name)
        if report is not None:
            out[name] = report
    return out


def ensure_report(name: str) -> dict[str, Any] | None:
    return once(name, ALL[name])
