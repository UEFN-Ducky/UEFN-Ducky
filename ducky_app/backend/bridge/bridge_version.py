"""Old-version bridges exit: only the running app's version talks to the listener.

After an update an IDE can keep an old tool server (bridge) alive, for example one
started from ``UEFN-Ducky-1.2.467.exe``, a file Setup never had to replace, and it
keeps connecting to the listener next to the new one. Here:

* the app records its version and pid in ``bridges/app.json`` and every bridge
  records its own in ``bridges/<pid>.json`` (``%LOCALAPPDATA%/UEFN-Ducky``)
* before a bridge's first listener POST, :func:`ensure_current` compares its version
  with the running app's: an older bridge refuses the call and exits (the IDE starts
  the current EXE again on the next tool call)
* the app (and the updater after an update) calls :func:`stop_old_bridges`: every
  live process registered with an older version, and every ``UEFN-Ducky*.exe`` that
  is older than the installed EXE (bridges from before this registry), is stopped

Never stops this process, the app itself, or anything that is not a Ducky EXE.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

APP_FILE = "app.json"
DIR_ENV = "UEFN_DUCKY_BRIDGE_DIR"
_EXIT_DELAY_S = 0.5

_checked = False
_check_lock = threading.Lock()


class OutdatedBridge(ConnectionError):
    """This bridge is older than the running app: it refuses calls and exits."""


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


def own_version() -> str:
    try:
        from frontend import __version__

        return str(__version__ or "")
    except Exception:
        return ""


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", str(version or "")))


def is_older(version: str, than: str) -> bool:
    """True when ``version`` is a known version strictly older than ``than``."""
    a, b = version_tuple(version), version_tuple(than)
    if not a or not b:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) < b + (0,) * (width - len(b))


# ---------------------------------------------------------------------------
# Registry: bridges/app.json and bridges/<pid>.json
# ---------------------------------------------------------------------------


def registry_dir() -> Path:
    override = os.environ.get(DIR_ENV, "").strip()
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "UEFN-Ducky" / "bridges"


def _create_time(pid: int) -> float:
    try:
        import psutil

        return float(psutil.Process(int(pid)).create_time())
    except Exception:
        return 0.0


def _alive(record: dict[str, Any]) -> bool:
    """The record's process still runs (same pid *and* same start time: no pid reuse)."""
    pid = int(record.get("pid") or 0)
    if pid <= 0:
        return False
    started = _create_time(pid)
    if not started:
        return False
    want = float(record.get("create_time") or 0.0)
    return not want or abs(started - want) < 2.0


def _write(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f"{path.name}.{os.getpid()}.part")
    part.write_text(json.dumps(record), encoding="utf-8")
    os.replace(part, path)


def _read(path: Path) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _record(role: str, version: str) -> dict[str, Any]:
    pid = os.getpid()
    return {
        "pid": pid,
        "version": version,
        "role": role,
        "exe": sys.executable,
        "create_time": _create_time(pid),
        "registered_at": time.time(),
    }


def note_running_app(version: str | None = None, *, root: Path | None = None) -> dict[str, Any]:
    """The app says which version is running now."""
    record = _record("app", own_version() if version is None else version)
    _write((root or registry_dir()) / APP_FILE, record)
    return record


def register_bridge(version: str | None = None, *, role: str = "tool_server", root: Path | None = None) -> dict[str, Any]:
    record = _record(role, own_version() if version is None else version)
    _write((root or registry_dir()) / f"{record['pid']}.json", record)
    return record


def unregister_bridge(pid: int | None = None, *, root: Path | None = None) -> None:
    try:
        ((root or registry_dir()) / f"{int(pid or os.getpid())}.json").unlink()
    except OSError:
        pass


def running_app(*, root: Path | None = None) -> Optional[dict[str, Any]]:
    """The live app's record, or None when no app runs (a bridge alone stays up)."""
    record = _read((root or registry_dir()) / APP_FILE)
    if record is None or not _alive(record):
        return None
    return record


def registered_bridges(*, root: Path | None = None) -> list[dict[str, Any]]:
    folder = root or registry_dir()
    out: list[dict[str, Any]] = []
    try:
        paths = sorted(folder.glob("*.json"))
    except OSError:
        return out
    for path in paths:
        if path.name == APP_FILE:
            continue
        record = _read(path)
        if record is not None:
            out.append(record)
    return out


# ---------------------------------------------------------------------------
# A bridge checks itself before it connects
# ---------------------------------------------------------------------------


def _packaged() -> bool:
    try:
        from frontend.bundle_root import is_packaged_runtime

        return bool(is_packaged_runtime())
    except Exception:
        return False


def registry_active(root: Path | None = None) -> bool:
    """Only an installed EXE (or an explicit registry folder) compares versions:
    source runs and test suites share no install, so they never stop anything."""
    return root is not None or bool(os.environ.get(DIR_ENV, "").strip()) or _packaged()


def _exit_soon() -> None:
    threading.Timer(_EXIT_DELAY_S, lambda: os._exit(0)).start()


def outdated_reason(version: str, app_version: str) -> str:
    return (
        f"This Ducky tool server is {version} but the running Ducky app is {app_version}: "
        "an old bridge left over from before the update. It exits now; the next tool "
        "call starts the current one. Retry the call."
    )


def ensure_current(
    *,
    role: str | None = None,
    version: str | None = None,
    root: Path | None = None,
    exit_fn: Callable[[], None] | None = None,
) -> None:
    """Once per process, before the first listener POST.

    The app records itself and stops older bridges (in the background). A bridge
    registers; when it is older than the running app it unregisters, schedules its
    own exit and raises :class:`OutdatedBridge` so the call never reaches the listener.
    """
    global _checked
    with _check_lock:
        if _checked:
            return
        _checked = True
    if not registry_active(root):
        return
    if role is None:
        from backend import process_role

        role = process_role.role()
    version = own_version() if version is None else version
    try:
        if role == "app":
            note_running_app(version, root=root)
            threading.Thread(
                target=stop_old_bridges, kwargs={"version": version, "root": root},
                name="ducky-stop-old-bridges", daemon=True,
            ).start()
            return
        app = running_app(root=root)
        if app is not None and is_older(version, str(app.get("version") or "")):
            unregister_bridge(root=root)
            (exit_fn or _exit_soon)()
            raise OutdatedBridge(outdated_reason(version, str(app.get("version"))))
        register_bridge(version, role=role, root=root)
    except OutdatedBridge:
        raise
    except Exception:
        # A registry that can't be read or written never blocks a tool call.
        pass


def reset_for_tests() -> None:
    global _checked
    with _check_lock:
        _checked = False


# ---------------------------------------------------------------------------
# The app (and the updater) stop older bridges
# ---------------------------------------------------------------------------


def _ducky_processes() -> Iterable[dict[str, Any]]:
    """``{pid, exe, create_time}`` for each running ``UEFN-Ducky*.exe`` (Windows EXE only)."""
    try:
        import psutil

        from frontend.app_paths import exe_name_matches
    except Exception:
        return []
    out = []
    for proc in psutil.process_iter(["pid", "name", "exe", "create_time"]):
        info = proc.info
        name = str(info.get("name") or "")
        if not name or not exe_name_matches(name) or "setup" in name.lower():
            continue
        out.append({"pid": int(info["pid"]), "exe": str(info.get("exe") or ""),
                    "create_time": float(info.get("create_time") or 0.0)})
    return out


def _installed_at(exe: str) -> float:
    """When the EXE file landed on disk. Setup keeps the build's mtime, so the
    creation time (Windows) is the install time; the later of the two wins."""
    try:
        st = Path(exe).stat()
    except OSError:
        return 0.0
    born = float(getattr(st, "st_birthtime", 0.0) or (st.st_ctime if sys.platform == "win32" else 0.0))
    return max(float(st.st_mtime), born)


def _terminate(pid: int) -> bool:
    try:
        import psutil

        psutil.Process(int(pid)).terminate()
        return True
    except Exception:
        return False


def stop_old_bridges(
    *,
    version: str | None = None,
    root: Path | None = None,
    processes: Callable[[], Iterable[dict[str, Any]]] | None = None,
    kill: Callable[[int], bool] | None = None,
    exe: str | None = None,
) -> list[int]:
    """Stop every bridge older than ``version`` (default: this process's). Returns the pids.

    1. Registered bridges with an older version that are still alive.
    2. ``UEFN-Ducky*.exe`` processes started before the current EXE was installed,
       or running another file than it: bridges from the old install that predate
       the registry. Only in the packaged EXE (``exe`` is a Ducky EXE).
    Never this process or the running app.
    """
    version = own_version() if version is None else version
    kill = kill or _terminate
    root = root or registry_dir()
    me = os.getpid()
    app = running_app(root=root)
    keep = {me, int((app or {}).get("pid") or 0)}
    current: set[int] = set()
    stopped: list[int] = []

    for record in registered_bridges(root=root):
        pid = int(record.get("pid") or 0)
        if pid in keep:
            continue
        if not _alive(record):
            unregister_bridge(pid, root=root)
            continue
        if is_older(str(record.get("version") or ""), version):
            if kill(pid):
                stopped.append(pid)
            unregister_bridge(pid, root=root)
        else:
            current.add(pid)

    exe = sys.executable if exe is None else exe
    try:
        from frontend.app_paths import exe_name_matches

        packaged = bool(exe) and exe_name_matches(Path(exe).name)
    except Exception:
        packaged = processes is not None
    if packaged:
        installed = _installed_at(exe)
        for proc in (processes or _ducky_processes)():
            pid = int(proc.get("pid") or 0)
            if pid in keep or pid in current or pid in stopped or pid <= 0:
                continue
            other_file = bool(proc.get("exe")) and os.path.normcase(str(proc["exe"])) != os.path.normcase(exe)
            before_install = installed and float(proc.get("create_time") or 0.0) < installed
            if (other_file or before_install) and kill(pid):
                stopped.append(pid)
    return stopped
