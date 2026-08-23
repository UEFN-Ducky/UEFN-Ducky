"""Deploy the listener stub + AppData listener. Quarantine stray island .py only."""

from __future__ import annotations

import json
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


def _frozen_init_text() -> str:
    return _resolve_frontend_file("frozen_init_unreal.py").read_text(encoding="utf-8")


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
            # Core tree already current — still refresh plugin overlays + user init.
            _overlay_plugin_listeners(dest)
            try:
                install_user_init_unreal()
            except Exception:
                pass
            try:
                install_toolset_listener_boot()
            except Exception:
                pass
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
    try:
        install_user_init_unreal()
    except Exception:
        pass
    try:
        install_toolset_listener_boot()
    except Exception:
        pass
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


_SWEEP_SKIP_DIRS = frozenset({"Saved", "Intermediate", "DerivedDataCache", ".git"})
_PY_FILE_SUFFIXES = frozenset({".py", ".pyc"})
# The only .py Ducky is allowed to keep in the island — listener boot after Content mounts.
_KEEP_ISLAND_PYTHON = frozenset({"content/python/init_unreal.py"})


def content_python_dir(project_root: Path) -> Path:
    return project_root / "Content" / "Python"


def _is_kept_island_python(project_root: Path, path: Path) -> bool:
    try:
        rel = path.relative_to(project_root.resolve()).as_posix().lower()
    except ValueError:
        return False
    return rel in _KEEP_ISLAND_PYTHON


def quarantine_python_root() -> Path:
    return appdata_uefn_ducky_dir() / "quarantined_python"


def _project_slug_for_quarantine(project_root: Path) -> str:
    try:
        from frontend.ui_web.project_chats import project_slug

        return project_slug(str(project_root))
    except Exception:
        name = project_root.name or "project"
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:40]
        return safe or "project"


def _is_python_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in _PY_FILE_SUFFIXES


def _walk_python_artifacts(base: Path, found: list[Path], project_root: Path) -> None:
    if not base.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in _SWEEP_SKIP_DIRS]
        for name in list(dirnames):
            if name == "__pycache__":
                found.append(Path(dirpath) / name)
                dirnames.remove(name)
        for name in filenames:
            path = Path(dirpath) / name
            if _is_python_file(path) and not _is_kept_island_python(project_root, path):
                found.append(path)


def _collect_python_artifacts(project_root: Path, *, deep: bool) -> list[Path]:
    root = project_root.resolve()
    found: list[Path] = []
    if deep:
        _walk_python_artifacts(root, found, root)
        return found
    # ponytail: shallow = project-root files + Content/Python/** + .ducky/**
    # Ceiling: a .py dropped deeper than depth 1 outside those two trees is only
    # caught by the next deploy sweep (deep=True). Upgrade: walk the whole
    # project on a background timer if agents keep dropping scratch mid-session.
    try:
        for child in root.iterdir():
            if child.is_dir() and child.name == "__pycache__":
                found.append(child)
            elif _is_python_file(child) and not _is_kept_island_python(root, child):
                found.append(child)
    except OSError:
        pass
    _walk_python_artifacts(root / "Content" / "Python", found, root)
    _walk_python_artifacts(root / ".ducky", found, root)
    return found


def _unique_quarantine_dest(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    n = 1
    while True:
        candidate = dest.with_name(f"{stem}__{n}{suffix}" if suffix else f"{dest.name}__{n}")
        if not candidate.exists():
            return candidate
        n += 1


def quarantine_project_python(project_root: Path, *, deep: bool) -> list[str]:
    """Move every ``.py`` / ``.pyc`` / ``__pycache__`` out of the island into AppData.

    Leaves Ducky's managed ``Content/Python/init_unreal.py`` in place. Extra
    ``.py`` / ``.pyc`` / ``__pycache__`` (agent scratch) is moved, never deleted.
    """
    try:
        root = Path(project_root).resolve()
    except OSError:
        return []
    if not root.is_dir():
        return []
    dest_root = quarantine_python_root() / _project_slug_for_quarantine(root)
    logs: list[str] = []
    for src in _collect_python_artifacts(root, deep=deep):
        if _is_kept_island_python(root, src):
            continue
        try:
            rel = src.relative_to(root)
        except ValueError:
            continue
        dest = dest_root / rel
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest = _unique_quarantine_dest(dest)
            shutil.move(str(src), str(dest))
        except OSError as exc:
            logs.append(f"Failed to quarantine {rel.as_posix()}: {exc}")
            continue
        logs.append(f"Quarantined {rel.as_posix()} -> {dest}")
    return logs


def enable_uefn_project_python(project_root: Path) -> str | None:
    """Flip Beta Access Python in ``*.uefnproject`` so the listener can run."""
    matches = sorted(project_root.glob("*.uefnproject"))
    if not matches:
        return None
    path = matches[0]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    py = (
        data.setdefault("dataSets", {})
        .setdefault("experimental", {})
        .setdefault("pythonExperimental", {})
    )
    if not isinstance(py, dict):
        return None
    if py.get("bEnablePythonForProject") is True:
        return None
    py["bEnablePythonForProject"] = True
    path.write_text(json.dumps(data, indent="\t") + "\n", encoding="utf-8")
    return f"Enabled Python Editor Scripting in {path.name}"


def _user_init_text() -> str:
    return _resolve_frontend_file("user_init_unreal.py").read_text(encoding="utf-8")


def documents_unreal_python_dir() -> Path:
    """Editor default path that auto-runs ``init_unreal.py`` (UE / UEFN)."""
    home = Path.home()
    return home / "Documents" / "UnrealEngine" / "Python"


_TOOLSET_BOOT_MARKER = "UEFN-Ducky listener boot (managed)"
_TOOLSET_BOOT_MODULE = "ducky_listener_boot"
_TOOLSET_BOOT_HOOK = (
    f"\n# --- {_TOOLSET_BOOT_MARKER} ---\n"
    "try:\n"
    f"    import {_TOOLSET_BOOT_MODULE}  # noqa: F401\n"
    "except Exception:\n"
    "    pass\n"
    f"# --- end {_TOOLSET_BOOT_MARKER} ---\n"
)


def _fortnite_engine_roots() -> list[Path]:
    roots: list[Path] = []
    env = (os.environ.get("UEFN_DUCKY_FORTNITE_ROOT") or "").strip()
    if env:
        roots.append(Path(env))
    for base in (
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    ):
        if base:
            roots.append(Path(base) / "Epic Games" / "Fortnite")
    return roots


def editor_toolset_python_dir() -> Path | None:
    """Epic EditorToolset Content/Python — the only init_unreal UEFN runs on ForceEnablePython."""
    rel = Path("Engine") / "Plugins" / "Experimental" / "Toolsets" / "EditorToolset" / "Content" / "Python"
    for root in _fortnite_engine_roots():
        candidate = root / rel
        if candidate.is_dir() and (candidate / "init_unreal.py").is_file():
            return candidate
    return None


def install_toolset_listener_boot() -> str | None:
    """Hook Epic EditorToolset init_unreal so Ducky starts when Toolsets ForceEnable Python.

    With UEFN MCP Toolsets, ForceEnablePythonAtRuntime only runs Engine plugin
    ``init_unreal.py`` scripts — not Documents/UnrealEngine/Python. Re-install
    on every deploy/launch (Fortnite updates wipe Engine/Plugins).
    """
    toolset_py = editor_toolset_python_dir()
    if toolset_py is None:
        return None
    try:
        boot_text = _user_init_text()
    except FileNotFoundError:
        return None
    boot_path = toolset_py / f"{_TOOLSET_BOOT_MODULE}.py"
    boot_path.write_text(boot_text, encoding="utf-8")
    init_path = toolset_py / "init_unreal.py"
    existing = init_path.read_text(encoding="utf-8")
    if _TOOLSET_BOOT_MARKER in existing and f"import {_TOOLSET_BOOT_MODULE}" in existing:
        return f"Toolset listener boot ok: {boot_path}"
    # Strip a previous broken hook then append a clean one.
    if _TOOLSET_BOOT_MARKER in existing:
        start = existing.find(f"# --- {_TOOLSET_BOOT_MARKER}")
        end = existing.find(f"# --- end {_TOOLSET_BOOT_MARKER}")
        if start >= 0 and end > start:
            end = existing.find("\n", end)
            existing = (existing[:start] + existing[end + 1 if end >= 0 else len(existing) :]).rstrip() + "\n"
    init_path.write_text(existing.rstrip() + _TOOLSET_BOOT_HOOK, encoding="utf-8")
    return f"Hooked UEFN-Ducky into EditorToolset init_unreal.py (+ {boot_path.name})"


def install_user_init_unreal() -> str | None:
    """Install Documents/UnrealEngine/Python/init_unreal.py so ForceEnablePython still starts us.

    UEFN often enables Python before the island Content mount. The Documents
    path is on the editor's default Python search list and runs at ForceEnable
    time. The island ``Content/Python/init_unreal.py`` stub is still installed
    on project open for when Content mounts first.
    """
    dest_dir = documents_unreal_python_dir()
    dest = dest_dir / "init_unreal.py"
    try:
        text = _user_init_text()
    except FileNotFoundError:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.read_text(encoding="utf-8").strip() == text.strip():
        return None
    # Preserve a non-Ducky init if the user already has one (append import once).
    if dest.is_file():
        existing = dest.read_text(encoding="utf-8")
        marker = "UEFN-Ducky user-level Python startup"
        if marker not in existing and "from listener.bootstrap import run" not in existing:
            alt = dest_dir / "ducky_init_unreal.py"
            alt.write_text(text, encoding="utf-8")
            if "import ducky_init_unreal" not in existing:
                shim = (
                    existing.rstrip()
                    + "\n\n# UEFN-Ducky — load AppData listener (ForceEnablePython race fix)\n"
                    "try:\n"
                    "    import ducky_init_unreal  # noqa: F401\n"
                    "except Exception:\n"
                    "    pass\n"
                )
                dest.write_text(shim, encoding="utf-8")
                return f"Hooked UEFN-Ducky into existing {dest}"
            return f"Updated {alt}"
    dest.write_text(text, encoding="utf-8")
    return f"Installed user init_unreal.py -> {dest}"


def ensure_frozen_init(project_root: Path) -> list[str]:
    """Install the island ``Content/Python/init_unreal.py`` listener stub."""
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
    """Write the island listener stub, then quarantine any other project ``.py``.

    Listener source is refreshed into ``%LOCALAPPDATA%/UEFN-Ducky/listener`` by
    ``sync_listener_to_appdata`` on UEFN-Ducky.exe / bridge launch. The only
    island Python file is ``Content/Python/init_unreal.py``.
    """
    logs = ensure_frozen_init(project_root)
    logs.extend(quarantine_project_python(project_root, deep=True))
    py_log = enable_uefn_project_python(project_root)
    if py_log:
        logs.append(py_log)
    try:
        user_log = install_user_init_unreal()
        if user_log:
            logs.append(user_log)
    except Exception:
        pass
    try:
        toolset_log = install_toolset_listener_boot()
        if toolset_log:
            logs.append(toolset_log)
    except Exception as exc:
        logs.append(f"Toolset listener boot hook failed: {exc}")
    try:
        from backend.mcp_plugins.epic import ensure_editor_auto_start

        if ensure_editor_auto_start():
            logs.append("Enabled Epic MCP Auto Start in UEFN Editor.ini")
    except Exception:
        pass
    logs.append(f"Listener port fixed at {int(listener_port)}")
    logs.append(
        "Listener: with UEFN MCP Toolsets, ForceEnablePython only runs Engine "
        "EditorToolset init_unreal — Ducky hooks that file (ducky_listener_boot). "
        "Restart UEFN after Deploy, or Tools → Execute Python Script → "
        "%LOCALAPPDATA%/UEFN-Ducky/listener/launch_listener.py"
    )

    from frontend.skill_deploy import sync_skill_on_mcp_update

    logs.extend(sync_skill_on_mcp_update())
    return logs
