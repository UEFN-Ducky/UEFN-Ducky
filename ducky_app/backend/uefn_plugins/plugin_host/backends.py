"""Importing a plugin backend: Python source, or a compiled .pyd and its quarantine."""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from typing import Any

from backend.uefn_plugins.store import appdata_uefn_plugins_dir


def _manifest_is_compiled(manifest: dict[str, Any]) -> bool:
    """True when plugin.json ships a compiled backend instead of .py source."""
    if bool(manifest.get("compiled")):
        return True
    if str(manifest.get("python_abi") or "").strip():
        return True
    backend = manifest.get("backend") if isinstance(manifest.get("backend"), dict) else {}
    return bool(str(backend.get("compiled") or "").strip())


def _compiled_backend_pyd(root: Path, manifest: dict[str, Any], mod_name: str) -> Path | None:
    """Locate the compiled backend .pyd at the plugin root.

    Prefers the exact ``backend.compiled`` path from plugin.json; falls back to the
    newest ``<mod_name>*.pyd`` for this ABI so a rename or missing field still loads.
    """
    backend = manifest.get("backend") if isinstance(manifest.get("backend"), dict) else {}
    rel = str(backend.get("compiled") or "").strip()
    if rel:
        p = root / rel
        if p.is_file():
            return p
    matches = sorted(
        (p for p in root.glob(f"{mod_name}*.pyd") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
    )
    return matches[-1] if matches else None


def _pyd_identity(pyd: Path) -> str:
    """Path + mtime: a newer build can land under the old file name."""
    try:
        return f"{pyd}|{pyd.stat().st_mtime_ns}"
    except OSError:
        return str(pyd)


def _import_compiled_backend(pid: str, root: Path, manifest: dict[str, Any]) -> Any:
    """Load a plugin's Nuitka-compiled backend extension module.

    The .pyd is loaded from the plugin root under the name it was built with: its own
    ``uefn_plugin_<id>_v<rank>`` (``backend.module``), or ``uefn_plugin_<id>`` for builds
    before that. Its ``__file__``-relative data paths resolve to the plugin folder exactly
    as the source package did. A versioned build loads fresh next to an older running
    copy (a Store update, no restart); ``uefn_plugin_<id>`` then points at the newest so
    unload() reaches it. An ABI it was not built for is refused with a plain message.
    """
    from backend.uefn_plugins.store import plugin_dir

    base_name = backend_module_name(pid)
    module_name = _built_module_name(manifest) or base_name
    loaded = _COMPILED_LOADED.get(module_name)
    if loaded is not None:
        mod, loaded_pyd = loaded
        pyd = _compiled_backend_pyd(root, manifest, base_name)
        if pyd is not None and _pyd_identity(pyd) != loaded_pyd:
            # Same name, new file (a rebuild of the same version): its init can't run again.
            label = str(manifest.get("label") or pid)
            _RESTART_PENDING[pid] = f"Restart UEFN Ducky to finish updating {label}"
        sys.modules[module_name] = mod
        sys.modules[base_name] = mod
        return mod
    if module_name in sys.modules:
        return sys.modules[module_name]

    abi = str(manifest.get("python_abi") or "").strip()
    if abi and abi != PY_ABI:
        raise RuntimeError(
            f"Update this plugin: it was built for {abi}, this Ducky runs {PY_ABI}."
        )

    # A newer version's .pyd may still be waiting in quarantine after a disable/update
    # while the old copy was mapped — restore it so the enabled plugin can load.
    _restore_compiled_from_quarantine(pid, plugin_dir(pid))
    pyd = _compiled_backend_pyd(root, manifest, base_name)
    if pyd is None:
        raise RuntimeError(f"compiled backend .pyd not found for {pid}")

    from importlib.machinery import ExtensionFileLoader

    loader = ExtensionFileLoader(module_name, str(pyd))
    spec = importlib.util.spec_from_file_location(module_name, str(pyd), loader=loader)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load compiled backend for {pid}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    _COMPILED_LOADED[module_name] = (mod, _pyd_identity(pyd))  # before exec: a failed init is still mapped
    spec.loader.exec_module(mod)
    sys.modules[base_name] = mod  # the plugin's unload()/lookups reach the newest copy
    return mod


def _built_module_name(manifest: dict[str, Any]) -> str:
    backend = manifest.get("backend") if isinstance(manifest.get("backend"), dict) else {}
    return str(backend.get("module") or "").strip()


def _import_backend(pid: str, root: Path, entry: str) -> Any:
    """Import plugin backend package from AppData (or plain module file)."""
    entry_path = root / entry
    module_name = backend_module_name(pid)
    if module_name in sys.modules:
        return sys.modules[module_name]

    if entry_path.is_dir() and (entry_path / "__init__.py").is_file():
        init_py = entry_path / "__init__.py"
        spec = importlib.util.spec_from_file_location(
            module_name,
            init_py,
            submodule_search_locations=[str(entry_path)],
        )
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        # Submodules resolve as uefn_plugin_<pid>.tools etc.
        spec.loader.exec_module(mod)
        return mod

    py_file = entry_path if entry_path.suffix == ".py" else Path(str(entry_path) + ".py")
    if not py_file.is_file():
        py_file = root / "backend" / "register.py"
    if not py_file.is_file():
        _log.warning("Plugin %s: backend entry not found (%s)", pid, entry)
        return None
    spec = importlib.util.spec_from_file_location(module_name, py_file)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _compiled_quarantine_dir() -> Path:
    return appdata_uefn_plugins_dir() / _COMPILED_QUARANTINE


def release_locked_compiled_backends(plugin_id: str, *, attempts: int = 12) -> list[Path]:
    """Move a plugin's compiled .pyd(s) out of its folder so the Store can overwrite it.

    A loaded extension stays mapped in-process and Windows locks the file, so the
    folder cannot be deleted in place. Renaming the file still works, so the old .pyd
    is moved into a quarantine folder; the next version installs under a new name and
    loads fresh, and the quarantined file is swept once Ducky restarts and unlocks it.
    Antivirus often holds a freshly loaded .pyd open for a moment, so a refused move is
    retried. Returns the .pyd files that still could not be moved (the caller must not
    wipe the folder then). Never raises into the Store install path.
    """
    import uuid

    from backend.uefn_plugins.store import _is_win_lock_error, normalize_plugin_id, plugin_dir

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError:
        return []
    root = plugin_dir(pid)
    if not root.is_dir():
        return []
    mod_name = backend_module_name(pid)
    quarantine = _compiled_quarantine_dir() / pid
    stuck: list[Path] = []
    for pyd in list(root.glob(f"{mod_name}*.pyd")):
        for i in range(max(1, attempts)):
            try:
                quarantine.mkdir(parents=True, exist_ok=True)
                # Prefix keeps the original name recoverable for restore; .bin so a swept
                # leftover is never mistaken for an installed module.
                dest = quarantine / f"{uuid.uuid4().hex}__{pyd.name}.bin"
                pyd.replace(dest)
                break
            except OSError as exc:
                if not _is_win_lock_error(exc) or i == attempts - 1:
                    _log.warning("Could not move compiled backend %s aside: %s", pyd, exc)
                    stuck.append(pyd)
                    break
                time.sleep(0.05 * (2 ** min(i, 5)))
    return stuck


def _restore_compiled_from_quarantine(pid: str, root: Path) -> None:
    """Recover a quarantined .pyd when the plugin folder has none. Best-effort.

    An install parks the mapped old .pyd in quarantine before overwriting the folder.
    If that overwrite was interrupted and no new .pyd was written, this brings the old
    one back so the plugin still loads. A completed install already wrote a new .pyd at
    the root, so this is a no-op then.
    """
    try:
        mod_name = backend_module_name(pid)
        if any(root.glob(f"{mod_name}*.pyd")):
            return
        quarantine = _compiled_quarantine_dir() / pid
        if not quarantine.is_dir():
            return
        parked = sorted(
            (p for p in quarantine.glob(f"*__{mod_name}*.pyd.bin") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        if not parked:
            return
        newest = parked[-1]
        # Recover the original file name (strip the "<uuid>__" prefix and ".bin").
        original = newest.name.split("__", 1)[1][: -len(".bin")]
        newest.replace(root / original)
    except OSError:
        _log.debug("Could not restore quarantined backend for %s", pid, exc_info=True)


def sweep_compiled_quarantine() -> None:
    """Delete quarantined .pyd files that have unlocked (previous run exited). Best-effort."""
    root = _compiled_quarantine_dir()
    if not root.is_dir():
        return
    for child in list(root.glob("*/*.pyd.bin")):
        try:
            child.unlink()
        except OSError:
            pass  # still mapped by a live process — try again next launch
    for pdir in list(root.glob("*")):
        try:
            if pdir.is_dir() and not any(pdir.iterdir()):
                pdir.rmdir()
        except OSError:
            pass


_ORPHAN_PYD_MIN_AGE_S = 600.0


def sweep_orphan_compiled_folders() -> None:
    """Delete plugin folders that hold nothing but an old compiled .pyd. Best-effort.

    An uninstall before the quarantine move existed deleted everything except the
    .pyd Windows still had locked, leaving a folder with no plugin.json that nothing
    lists or cleans. Only folders made up entirely of ``uefn_plugin_*.pyd`` files at
    least 10 minutes old go, so an install that is still unpacking is never touched.
    """
    root = appdata_uefn_plugins_dir()
    if not root.is_dir():
        return
    now = time.time()
    for pdir in list(root.iterdir()):
        try:
            if not pdir.is_dir() or pdir.name.startswith(".") or (pdir / "plugin.json").exists():
                continue
            files = list(pdir.iterdir())
            if not files or not all(
                f.is_file()
                and f.name.startswith("uefn_plugin_")
                and f.suffix == ".pyd"
                and now - f.stat().st_mtime >= _ORPHAN_PYD_MIN_AGE_S
                for f in files
            ):
                continue
            for f in files:
                f.unlink()
            pdir.rmdir()
        except OSError:
            pass  # still mapped by a live process — try again next launch


# Names from the rest of the host, bound after every definition above so the host
# modules can import each other in any order.
from backend.uefn_plugins.plugin_host.loader import backend_module_name  # noqa: E402
from backend.uefn_plugins.plugin_host.state import (  # noqa: E402
    _COMPILED_LOADED,
    _COMPILED_QUARANTINE,
    _RESTART_PENDING,
    PY_ABI,
    _log,
)
