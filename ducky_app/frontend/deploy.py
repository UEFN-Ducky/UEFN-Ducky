"""Deploy frozen init_unreal.py into a UEFN project (once). Bootstrap lives in AppData."""

from __future__ import annotations

import os
import shutil
import sys
import threading
from pathlib import Path

from frontend.bundle_root import is_packaged_runtime, packaged_data_root


def bundled_listener_dir() -> Path | None:
    """When packaged, listener source lives under ``bundle/uefn_listener`` (PyInstaller or Nuitka)."""
    if not is_packaged_runtime():
        return None
    base = packaged_data_root()
    if not base:
        return None
    listener = base / "bundle" / "uefn_listener"
    if listener.is_dir() and (listener / "listener").is_dir():
        return listener
    return None


def _panel_dir() -> Path:
    return Path(__file__).resolve().parent


def _resolve_frontend_file(name: str) -> Path:
    """Dev: ``frontend/<name>``. Frozen: ``<_MEIPASS>/frontend/<name>``."""
    if is_packaged_runtime():
        base = packaged_data_root()
        if base:
            candidate = base / "frontend" / name
            if candidate.is_file():
                return candidate
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidate = Path(meipass) / "frontend" / name
            if candidate.is_file():
                return candidate
    candidate = _panel_dir() / name
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(f"frontend/{name} not found (rebuild UEFN-Ducky.exe)")


def _frozen_init_text() -> str:
    return _resolve_frontend_file("frozen_init_unreal.py").read_text(encoding="utf-8")


def appdata_uefn_ducky_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "UEFN-Ducky"


def appdata_listener_dir() -> Path:
    return appdata_uefn_ducky_dir() / "listener"


def _source_listener_dir() -> Path | None:
    """Plaintext listener source: ``bundle/uefn_listener`` when packaged, else ``ducky_app/uefn_listener``.

    ``UEFN_DUCKY_LISTENER_SRC`` wins when set (dev race-fix / pin a checkout) so a frozen
    EXE cannot overwrite AppData with a stale bundled listener.
    """
    override = (os.environ.get("UEFN_DUCKY_LISTENER_SRC") or "").strip()
    if override:
        p = Path(override)
        if (p / "listener").is_dir():
            return p
        if p.name == "listener" and p.is_dir():
            return p.parent
    packaged = bundled_listener_dir()
    if packaged is not None:
        return packaged
    repo = _panel_dir().parent / "uefn_listener"
    return repo if (repo / "listener").is_dir() else None


def listener_tree_stamp(root: Path) -> str:
    """``"<py file count>:<max mtime>"`` — MUST match ``listener/source_stamp.compute_stamp``.

    The listener reports the stamp it was imported from (ping.source_stamp); the host
    computes this over AppData. Mismatch = deployed-but-not-loaded → auto reload_listener.
    """
    latest = 0.0
    count = 0
    try:
        for dirpath, dirnames, filenames in os.walk(root / "listener"):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in filenames:
                if not name.endswith(".py"):
                    continue
                try:
                    m = (Path(dirpath) / name).stat().st_mtime
                except OSError:
                    continue
                count += 1
                if m > latest:
                    latest = m
    except OSError:
        pass
    return f"{count}:{latest:.0f}"


_DEPLOY_STAMP_NAME = ".deploy_stamp"


def _source_recency(src: Path) -> float:
    """How new the source is, for newest-wins. Frozen bundle mtimes are extraction-time
    junk, so a packaged build's recency is the EXE's own mtime (build/install time)."""
    if is_packaged_runtime() and bundled_listener_dir() is not None and src == bundled_listener_dir():
        try:
            return Path(sys.executable).stat().st_mtime
        except OSError:
            return 0.0
    latest = 0.0
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name.endswith(".py"):
                try:
                    latest = max(latest, (Path(dirpath) / name).stat().st_mtime)
                except OSError:
                    continue
    return latest


def _dest_recency(dest: Path) -> float:
    stamp = dest / _DEPLOY_STAMP_NAME
    try:
        return float(stamp.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pass
    # No stamp file (e.g. a manual hot-deploy copy) — fall back to tree mtimes.
    return _source_recency(dest) if dest.is_dir() else 0.0


def _overlay_plugin_listeners(listener_root: Path) -> bool:
    """Copy enabled plugins' ``listener/`` folders into ``listener/plugins/<id>/``.

    Returns True when the overlay tree changed (or was written). Safe to call on
    an already-deployed AppData tree or a staging copy before atomic swap.
    """
    try:
        from backend.uefn_plugins.store import (
            appdata_uefn_plugins_dir,
            get_enabled_plugin_ids,
            load_plugin_manifest,
        )
    except Exception:
        return False

    plugins_pkg = listener_root / "listener" / "plugins"
    plugins_pkg.mkdir(parents=True, exist_ok=True)
    init_py = plugins_pkg / "__init__.py"
    if not init_py.is_file():
        # Staging from an older source tree — ensure the loader package exists.
        init_py.write_text(
            '"""Store plugin listener handlers (populated by deploy overlay)."""\n'
            "from __future__ import annotations\n"
            "import importlib\n"
            "import pkgutil\n"
            "from pathlib import Path\n"
            "_ROOT = Path(__file__).resolve().parent\n"
            "for _info in pkgutil.iter_modules([str(_ROOT)]):\n"
            "    if not _info.name or _info.name.startswith('_'):\n"
            "        continue\n"
            "    try:\n"
            "        importlib.import_module(f'listener.plugins.{_info.name}')\n"
            "    except Exception:\n"
            "        pass\n",
            encoding="utf-8",
        )

    wanted: set[str] = set()
    changed = False
    root = appdata_uefn_plugins_dir()
    for pid in get_enabled_plugin_ids():
        manifest = load_plugin_manifest(pid)
        if not manifest:
            continue
        src = root / pid / "listener"
        if not src.is_dir():
            continue
        # Hyphens are valid plugin ids but not Python package names.
        pkg_name = pid.replace("-", "_")
        wanted.add(pkg_name)
        dest = plugins_pkg / pkg_name
        try:
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            # Ensure importable as listener.plugins.<pkg_name>
            pkg_init = dest / "__init__.py"
            if not pkg_init.is_file():
                pkg_init.write_text(
                    '"""Plugin listener handlers — import side effects register commands."""\n',
                    encoding="utf-8",
                )
            changed = True
        except OSError:
            continue

    # Drop overlays for plugins that are no longer enabled / no longer ship listener/.
    try:
        for child in list(plugins_pkg.iterdir()):
            if not child.is_dir() or child.name.startswith("_"):
                continue
            if child.name not in wanted:
                shutil.rmtree(child, ignore_errors=True)
                changed = True
    except OSError:
        pass
    return changed


def overlay_plugin_listeners_to_appdata(*, reload: bool = True) -> bool:
    """Re-apply plugin listener overlays into the live AppData listener tree.

    Called after Store install/enable/disable so UEFN picks up new handlers
    without waiting for the next full sync. Optionally fires ``reload_listener``.
    """
    dest = appdata_listener_dir()
    if not (dest / "listener").is_dir():
        # Nothing deployed yet — next sync_listener_to_appdata will overlay.
        synced = sync_listener_to_appdata()
        if synced is None:
            return False
        dest = synced
    changed = _overlay_plugin_listeners(dest)
    if changed:
        # Bump stamp so ship_newest / ping comparisons see a new tree.
        try:
            stamp = dest / _DEPLOY_STAMP_NAME
            stamp.write_text(f"{_source_recency(dest):.3f}", encoding="utf-8")
        except OSError:
            pass
    if reload:
        try:
            from backend.bridge import send_command

            send_command("reload_listener", timeout=6.0)
        except Exception:
            pass
    return changed


def sync_listener_to_appdata() -> Path | None:
    """Copy the listener source to ``%LOCALAPPDATA%/UEFN-Ducky/listener`` — NEWEST WINS.

    Called on UEFN-Ducky.exe / bridge launch. An old frozen exe must never clobber newer
    code already in AppData (repo hot-deploys, a newer exe's deploy) — that race shipped
    stale crash-prone handlers mid-session on 2026-07-11. ``UEFN_DUCKY_LISTENER_SRC``
    stays authoritative (explicit pin → always overwrites).

    After the core tree is in place, enabled Store plugins' ``listener/`` folders are
    overlaid into ``listener/plugins/<plugin_id>/``.
    """
    src = _source_listener_dir()
    if src is None:
        return None
    dest = appdata_listener_dir()

    pinned = bool((os.environ.get("UEFN_DUCKY_LISTENER_SRC") or "").strip())
    src_recency = _source_recency(src)
    if dest.is_dir():
        dest_recency = _dest_recency(dest)
        if abs(dest_recency - src_recency) <= 0.001 or (not pinned and dest_recency > src_recency):
            # Core tree already current — still refresh plugin overlays.
            _overlay_plugin_listeners(dest)
            return dest

    # Every panel and coding-agent bridge is a separate process. A shared
    # listener.tmp lets simultaneous startups delete/copy each other's partial
    # trees, which can remove listener/config.py during hot reload.
    tmp = dest.with_name(f"listener.tmp.{os.getpid()}.{threading.get_ident()}")
    try:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(src, tmp, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if not (tmp / "listener" / "__init__.py").is_file() or not (tmp / "listener" / "config.py").is_file():
            raise OSError("Listener source copy is incomplete")
        _overlay_plugin_listeners(tmp)
        (tmp / _DEPLOY_STAMP_NAME).write_text(f"{src_recency:.3f}", encoding="utf-8")
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        os.replace(tmp, dest)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        return None
    return dest


def resolve_uefn_project_root(user_selection: Path) -> Path:
    p = user_selection.resolve()
    if p.is_file() and p.suffix.lower() == ".uefnproject":
        return p.parent
    if p.is_dir():
        if (p / ".uefnproject").is_file():
            return p
        if (p / "Content").is_dir():
            return p
    raise ValueError(
        "Select your UEFN project folder (should contain .uefnproject or a Content folder), "
        "or pick the .uefnproject file."
    )


def content_python_dir(project_root: Path) -> Path:
    return project_root / "Content" / "Python"


def ensure_frozen_init(project_root: Path) -> list[str]:
    """Install frozen ``init_unreal.py`` when content differs from the bundled stub."""
    dest_py = content_python_dir(project_root)
    dest_py.mkdir(parents=True, exist_ok=True)
    dest_init = dest_py / "init_unreal.py"
    frozen = _frozen_init_text()

    logs: list[str] = []
    if dest_init.is_file() and dest_init.read_text(encoding="utf-8").strip() == frozen.strip():
        logs.append(f"init_unreal.py already up to date: {dest_init}")
    else:
        dest_init.write_text(frozen, encoding="utf-8")
        logs.append(f"Installed init_unreal.py -> {dest_init}")
    return logs


def deploy_listener(project_root: Path, listener_port: int) -> list[str]:
    """Write ``init_unreal.py`` that loads the listener from AppData.

    Listener source is refreshed into ``%LOCALAPPDATA%/UEFN-Ducky/listener`` by
    ``sync_listener_to_appdata`` on UEFN-Ducky.exe / bridge launch — not written here.
    """
    logs = ensure_frozen_init(project_root)
    logs.append(f"Listener port fixed at {int(listener_port)}")
    logs.append("Listener source: start UEFN-Ducky.exe (panel or IDE bridge), then restart UEFN or reload_listener")

    from frontend.skill_deploy import sync_skill_on_mcp_update

    logs.extend(sync_skill_on_mcp_update())
    return logs


def _is_ducky_init_script(text: str) -> bool:
    markers = (
        "UEFN-Ducky",
        "UEFN_DUCKY",
        "listener.bootstrap",
        "[MCP] init_unreal",
    )
    return any(m in text for m in markers)


def remove_project_init_script(project_root: Path) -> bool:
    """Remove Ducky's ``init_unreal.py`` from the project (script only — not Content/Python/)."""
    dest_init = content_python_dir(project_root) / "init_unreal.py"
    try:
        if dest_init.is_file():
            existing = dest_init.read_text(encoding="utf-8")
            if _is_ducky_init_script(existing):
                dest_init.unlink()
                return True
    except OSError:
        pass
    return False
