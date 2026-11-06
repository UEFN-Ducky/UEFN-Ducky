"""Versions Ducky downloads and switches to by itself: no Setup, no Windows prompt.

    %LOCALAPPDATA%\\UEFN-Ducky\\versions\\
        app-1.2.411\\            a complete app folder (UEFN-Ducky.exe, -Bridge.exe, _internal\\)
            .ready              written last, once every file checked out
        app-1.2.412.partial\\    a download in progress
        boot.json               starts per version that never reached ready, and bad versions
        handover.json           window state left by the version that switched to a newer one
        current.json            the folder a start lands on, for Ducky-Launcher.exe (no Python)

Whatever EXE gets started (the installed one, a pin, ``ducky.cmd``, an IDE's MCP config)
hands off to the newest ready version that isn't bad, so every start lands on the
newest Ducky while every saved path keeps naming the installed EXE. Stdlib only: the
hand-off runs before anything else is imported.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from frontend.app_paths import APP_DATA_DIR_NAME, local_app_data_base

VERSIONS_DIR_NAME = "versions"
READY_MARKER = ".ready"
PARTIAL_SUFFIX = ".partial"
BOOT_FILE = "boot.json"
HANDOVER_FILE = "handover.json"
# {"version", "dir", "written", "installed"}: "dir" is relative to versions_dir().parent
# for a downloaded version, or the installed EXE's absolute folder. Older versions never
# read it: a new, additive file.
CURRENT_FILE = "current.json"
# The EXE that was started, before any hand-off: saved paths (IDE configs, Open with,
# the uefn-ducky:// link) must name it, never a folder under versions\.
LAUNCHER_ENV = "UEFN_DUCKY_LAUNCHER_EXE"
# Tests and sandboxed builds can turn the hand-off off.
NO_HOP_ENV = "UEFN_DUCKY_NO_VERSION_HOP"
# A version started this many times without reaching ready is bad: starts skip it.
MAX_UNREADY_STARTS = 2
# The optional native window host. Never a saved path: shortcuts, the Run key, Open with
# and uefn-ducky:// stay on UEFN-Ducky.exe, which decides per start whether to use it.
NATIVE_LAUNCHER_EXE = "Ducky-Launcher.exe"
APP_EXE = "UEFN-Ducky.exe"
# Argument the launcher starts UEFN-Ducky.exe with: the window lives in the launcher.
BACKEND_ARG = "--backend"
# frontend.autostart's names (this module stays stdlib only): a start hidden in the tray.
START_HIDDEN_ENV = "UEFN_DUCKY_START_HIDDEN"
BACKGROUND_ARG = "--background"
# launcher.py's ``--after-update <pid>``: wait for that process to end first.
AFTER_UPDATE_ARG = "--after-update"

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_DIR_RE = re.compile(r"^app-(\d+\.\d+\.\d+)$")
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_DETACHED_PROCESS = 0x00000008
_jobs: list[int] = []
_log = logging.getLogger(__name__)


def parse_version(text: str) -> tuple[int, int, int] | None:
    m = _VERSION_RE.match(str(text or "").strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def versions_dir() -> Path:
    return local_app_data_base() / APP_DATA_DIR_NAME / VERSIONS_DIR_NAME


def version_dir(version: str) -> Path:
    return versions_dir() / f"app-{version}"


def is_ready(folder: Path) -> bool:
    return (folder / READY_MARKER).is_file() and (folder / "UEFN-Ducky.exe").is_file()


def ready_versions() -> list[tuple[str, Path]]:
    """Ready version folders, newest first."""
    root = versions_dir()
    found: list[tuple[tuple[int, int, int], str, Path]] = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    for entry in entries:
        m = _DIR_RE.match(entry.name)
        if not m or not entry.is_dir() or not is_ready(entry):
            continue
        key = parse_version(m.group(1))
        if key is not None:
            found.append((key, m.group(1), entry))
    found.sort(reverse=True)
    return [(version, folder) for _, version, folder in found]


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def read_boot() -> dict[str, Any]:
    try:
        data = json.loads((versions_dir() / BOOT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _boot_row(boot: dict[str, Any], version: str) -> dict[str, Any]:
    row = boot.get(version)
    return row if isinstance(row, dict) else {}


def is_bad(version: str, boot: dict[str, Any] | None = None) -> bool:
    return bool(_boot_row(read_boot() if boot is None else boot, version).get("bad"))


def mark_bad(version: str, reason: str) -> None:
    boot = read_boot()
    row = _boot_row(boot, version)
    row.update(bad=True, reason=str(reason)[:200], at=time.time())
    boot[version] = row
    try:
        _write_json(versions_dir() / BOOT_FILE, boot)
    except OSError:
        pass


def record_start(version: str) -> bool:
    """Count a start of ``version`` (one that ends at :func:`mark_ready`). False, and the
    version is marked bad, when it already failed to start :data:`MAX_UNREADY_STARTS` times."""
    boot = read_boot()
    row = _boot_row(boot, version)
    if row.get("bad"):
        return False
    starts = int(row.get("starts") or 0)
    if starts >= MAX_UNREADY_STARTS:
        row.update(bad=True, reason=f"did not start {starts} times", at=time.time())
        ok = False
    else:
        row["starts"] = starts + 1
        ok = True
    boot[version] = row
    try:
        _write_json(versions_dir() / BOOT_FILE, boot)
    except OSError:
        pass
    return ok


def mark_ready(version: str) -> None:
    """The panel of ``version`` came up: its failed-start count goes back to zero."""
    write_current(version)
    boot = read_boot()
    row = _boot_row(boot, version)
    if not row.get("starts"):
        return
    row["starts"] = 0
    boot[version] = row
    try:
        _write_json(versions_dir() / BOOT_FILE, boot)
    except OSError:
        pass


def newest_usable(current: str) -> tuple[str, Path] | None:
    """The newest ready, not-bad version newer than ``current``."""
    mine = parse_version(current)
    if mine is None:
        return None
    boot = read_boot()
    for version, folder in ready_versions():
        key = parse_version(version)
        if key is None or key <= mine:
            break
        if not is_bad(version, boot):
            return version, folder
    return None


def read_current() -> dict[str, Any]:
    try:
        data = json.loads((versions_dir() / CURRENT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_current(version: str, installed: str = "") -> None:
    """Point current.json at the folder starts land on: ``version``'s folder when it is
    ready and not bad, else the installed EXE's folder. ``installed`` is the installed
    EXE's version (kept from the file when not given). Never raises."""
    if not getattr(sys, "frozen", False) or os.environ.get(NO_HOP_ENV):
        return  # dev runs and sandboxed builds never repoint the real install
    try:
        old = read_current()
        installed = installed or str(old.get("installed") or "")
        folder = version_dir(version) if parse_version(version) else None
        if folder is not None and is_ready(folder) and not is_bad(version):
            where = f"{VERSIONS_DIR_NAME}/{folder.name}"
        else:
            version, where = installed, str(launcher_exe().parent)
        data = {"version": version, "dir": where, "installed": installed}
        if {k: old.get(k) for k in data} == data:
            return
        _write_json(versions_dir() / CURRENT_FILE, {**data, "written": time.time()})
    except Exception:
        _log.warning("versions/current.json could not be written", exc_info=True)


def repoint_to_installed_pick() -> None:
    """Rewrite current.json to what the installed EXE would start now (after a rollback)."""
    installed = str(read_current().get("installed") or "")
    target = newest_usable(installed) if installed else None
    write_current(target[0] if target else installed, installed)


def backend_mode(argv: list[str] | None = None) -> bool:
    """True when this process is the backend of Ducky-Launcher.exe (it owns our pipes)."""
    return BACKEND_ARG in (sys.argv[1:] if argv is None else argv)


def launcher_exe() -> Path:
    """The EXE that was started before any hand-off (this one when nothing handed off).

    Never ``Ducky-Launcher.exe``: when the native launcher started us, saved paths and
    restarts name its sibling UEFN-Ducky.exe."""
    raw = (os.environ.get(LAUNCHER_ENV) or "").strip()
    exe = Path(raw) if raw else Path(sys.executable)
    if exe.name.lower() == NATIVE_LAUNCHER_EXE.lower():
        return exe.with_name(APP_EXE)
    return exe


def stable_exe(name: str | None = None) -> Path:
    """The EXE saved paths should name: the started one, or its sibling ``name``."""
    exe = launcher_exe()
    return exe.with_name(name) if name else exe


def running_from_versions() -> bool:
    try:
        return Path(sys.executable).resolve().is_relative_to(versions_dir().resolve())
    except (OSError, ValueError):
        return False


def hand_off_env() -> dict[str, str]:
    env = dict(os.environ)
    env[LAUNCHER_ENV] = str(launcher_exe())
    return env


def _kill_with_me(proc: subprocess.Popen) -> None:
    """Put ``proc`` in a job closed with this process, so an IDE stopping the bridge it
    started also stops the newer bridge this one handed off to."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    class _Basic(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _Io(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("r", "w", "o", "rb", "wb", "ob")]

    class _Extended(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _Basic),
            ("IoInfo", _Io),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return
    info = _Extended()
    # KILL_ON_JOB_CLOSE, and BREAKAWAY_OK so the shared MCP daemon it may start can leave.
    info.BasicLimitInformation.LimitFlags = 0x2000 | 0x0800
    kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
    kernel32.AssignProcessToJobObject(job, int(proc._handle))  # type: ignore[attr-defined]
    _jobs.append(job)


def _std(stream: Any) -> Any:
    try:
        return stream.fileno() if stream is not None else None
    except (OSError, ValueError, AttributeError):
        return None


def run_attached(exe: Path, args: list[str]) -> int:
    """Run ``exe`` on this process's stdin/stdout/stderr (an IDE's MCP pipes) and return its exit code."""
    proc = subprocess.Popen(
        [str(exe), *args],
        env=hand_off_env(),
        stdin=_std(sys.stdin),
        stdout=_std(sys.stdout),
        stderr=_std(sys.stderr),
    )
    try:
        _kill_with_me(proc)
    except Exception:
        pass
    try:
        return int(proc.wait())
    except KeyboardInterrupt:
        proc.terminate()
        return 1


def start_detached(exe: Path, args: list[str], *, env: dict[str, str] | None = None) -> bool:
    """Start ``exe`` on its own, outliving this process (and any job this one is in)."""
    flags = 0
    if sys.platform == "win32":
        flags = _CREATE_NEW_PROCESS_GROUP | _DETACHED_PROCESS
    for extra in (_CREATE_BREAKAWAY_FROM_JOB, 0) if sys.platform == "win32" else (0,):
        try:
            subprocess.Popen(
                [str(exe), *args],
                env=env or hand_off_env(),
                cwd=str(exe.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags | extra,
                close_fds=True,
            )
            return True
        except OSError:
            continue
    return False


def hand_off(current: str, *, bridge: bool) -> None:
    """Start the newest usable version instead of this one, then leave. Returns when
    this EXE is the one to run (nothing newer, a dev run, or hand-off turned off)."""
    if not getattr(sys, "frozen", False) or os.environ.get(NO_HOP_ENV):
        return
    installed = "" if running_from_versions() else current
    for _ in range(4):
        target = newest_usable(current)
        if target is None:
            break
        version, folder = target
        exe = folder / Path(sys.executable).name
        if not exe.is_file():
            break
        if bridge:
            write_current(version, installed)
            raise SystemExit(run_attached(exe, sys.argv[1:]))
        if not record_start(version):
            continue  # failed to start before: try the next one down
        write_current(version, installed)
        if backend_mode():
            # Ducky-Launcher.exe talks to us over stdin/stdout: the newer version must
            # keep those pipes, so it runs attached instead of detached.
            raise SystemExit(run_attached(exe, sys.argv[1:]))
        if start_detached(exe, _detached_args(sys.argv[1:])):
            os._exit(0)
        break
    write_current(current, installed)  # this EXE runs


def _detached_args(args: list[str]) -> list[str]:
    """``args`` for the next EXE. launcher.py takes ``--background`` out of argv before the
    hand-off (START_HIDDEN_ENV says it was there): put it back, or a sign-in start would
    open a window once it lands on a newer version."""
    args = list(args)
    if os.environ.get(START_HIDDEN_ENV) == "1" and BACKGROUND_ARG not in args:
        args.append(BACKGROUND_ARG)
    return args


def failed_while_starting(version: str) -> bool:
    """True while ``version``'s start is still open: started by a hand-off
    (:func:`record_start`) and its panel not up yet (:func:`mark_ready`)."""
    try:
        return int(_boot_row(read_boot(), version).get("starts") or 0) > 0
    except (TypeError, ValueError):
        return False


def fall_back_after_failed_start(current: str, reason: str) -> bool:
    """A downloaded version crashed while starting: mark it bad and start the EXE that was
    launched, which hands off to the next version down. True when the fallback started.

    A rollback never starts Ducky-Launcher.exe. As its backend, this only marks the
    version bad: the launcher then exits with an error and the UEFN-Ducky.exe that
    started it falls back on its own, so there is one window, not two."""
    if not running_from_versions():
        return False
    mark_bad(current, reason)
    repoint_to_installed_pick()
    if backend_mode():
        return False
    exe = launcher_exe()
    if not exe.is_file():
        return False
    # --after-update: the EXE waits for this process to end before it starts (every version
    # reads it). Started at once, it saw this pid alive in boot.json and turned its own crash
    # record off for the whole session.
    return start_detached(exe, _detached_args([AFTER_UPDATE_ARG, str(os.getpid())]))


def write_handover(state: dict[str, Any]) -> None:
    _write_json(versions_dir() / HANDOVER_FILE, {**state, "at": time.time()})


def take_handover(max_age_s: float = 120.0) -> dict[str, Any]:
    """Window state the previous version left when it switched to this one (read once)."""
    path = versions_dir() / HANDOVER_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    try:
        path.unlink()
    except OSError:
        pass
    if not isinstance(data, dict) or time.time() - float(data.get("at") or 0) > max_age_s:
        return {}
    return data


def wait_for_exit(pid: int, timeout_s: float = 60.0) -> None:
    """Wait for the version that switched to this one to finish closing."""
    if pid <= 0 or sys.platform != "win32":
        return
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(0x00100000, False, int(pid))  # SYNCHRONIZE
    if not handle:
        return
    try:
        kernel32.WaitForSingleObject(ctypes.c_void_p(handle), int(timeout_s * 1000))
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
