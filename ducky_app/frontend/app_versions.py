"""Versions Ducky downloads and switches to by itself: no Setup, no Windows prompt.

    %LOCALAPPDATA%\\UEFN-Ducky\\versions\\
        app\\                    the version that runs: always this one path (promote)
            .ready              written last, once every file checked out ({"version": ...})
        app.next\\               the downloaded update (UEFN-Ducky.exe, -Bridge.exe, _internal\\)
        app.next.partial\\       that download in progress
        app.previous\\           the version before, the rollback
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
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from frontend.app_paths import APP_DATA_DIR_NAME, local_app_data_base

VERSIONS_DIR_NAME = "versions"
READY_MARKER = ".ready"
PARTIAL_SUFFIX = ".partial"
# One app folder, as VS Code has one install folder. Windows keeps its allows by path
# (Controlled folder access, the firewall): a new app-<v> folder per update asked again
# after every update.
STABLE_DIR_NAME = "app"  # the version that runs
NEXT_DIR_NAME = "app.next"  # the downloaded update, until the switch moves it into app
PREVIOUS_DIR_NAME = "app.previous"  # the version before, the rollback
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
# The programs an app folder runs; a running one keeps the folder from being swapped.
EXE_NAMES = (APP_EXE, "UEFN-Ducky-Bridge.exe", NATIVE_LAUNCHER_EXE)
# Set by wait_for_exit: this start follows a version that closed for an update or rollback.
_after_update = False

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


def stable_dir() -> Path:
    return versions_dir() / STABLE_DIR_NAME


def next_dir() -> Path:
    return versions_dir() / NEXT_DIR_NAME


def previous_dir() -> Path:
    return versions_dir() / PREVIOUS_DIR_NAME


def folder_version(folder: Path) -> str:
    """The version a folder holds: from an older ``app-<v>`` name, else its ``.ready``
    marker; "" when unknown."""
    m = _DIR_RE.match(folder.name)
    if m:
        return m.group(1)
    try:
        version = str(json.loads((folder / READY_MARKER).read_text(encoding="utf-8")).get("version") or "")
    except (OSError, ValueError, AttributeError):
        return ""
    return version if parse_version(version) else ""


def version_dir(version: str) -> Path:
    """The folder holding ``version``: app, app.next or app.previous (or an older
    ``app-<version>``, the name it would have had)."""
    for folder in (stable_dir(), next_dir(), previous_dir()):
        if version and folder_version(folder) == version:
            return folder
    return versions_dir() / f"app-{version}"


def in_use(folder: Path) -> bool:
    """A Ducky program runs from ``folder``: Windows refuses to open a running EXE for
    writing. (Renaming its folder would still work, and that program's next read by path
    would then load another version's code.)"""
    for name in EXE_NAMES:
        try:
            with open(folder / name, "r+b"):
                pass
        except FileNotFoundError:
            continue
        except OSError:
            return True
    return False


def _image_path(pid: int) -> str:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(ctypes.c_void_p(handle), 0, buf, ctypes.byref(size)):
            return buf.value
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
    return ""


def processes_in(folder: Path) -> list[int]:
    """Pids of Ducky programs (not this one) running from ``folder``."""
    if sys.platform != "win32":
        return []
    import csv

    try:
        root = os.path.normcase(str(folder.resolve())) + os.sep
    except OSError:
        return []
    found: list[int] = []
    for name in EXE_NAMES:
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, errors="replace", timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        for row in csv.reader((out or "").splitlines()):
            if len(row) > 1 and row[0].lower() == name.lower() and row[1].isdigit() and int(row[1]) != os.getpid():
                try:
                    if os.path.normcase(_image_path(int(row[1]))).startswith(root):
                        found.append(int(row[1]))
                except (OSError, AttributeError, ValueError):
                    continue
    return found


def close_processes_in(folder: Path, grace_s: float = 5.0) -> None:
    """What VS Code's updater does before it swaps: give Ducky programs still running from
    ``folder`` (an IDE's bridge, an agent host, the old window) a moment to close, then
    stop them. An IDE starts its bridge again on its next tool call."""
    deadline = time.monotonic() + grace_s
    while in_use(folder) and time.monotonic() < deadline:
        time.sleep(0.25)
    for pid in processes_in(folder) if in_use(folder) else []:
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            pass
    deadline = time.monotonic() + 5.0
    while in_use(folder) and time.monotonic() < deadline:
        time.sleep(0.25)


def _link_or_copy(src: str, dst: str) -> None:
    try:
        os.link(src, dst)  # same disk: no extra space, and works on a running program's files
    except OSError:
        shutil.copy2(src, dst)


def discard(folder: Path) -> None:
    """Delete ``folder``, renamed aside first so a half-deleted copy never keeps its name."""
    if not folder.exists():
        return
    doomed = folder.with_name(f"{STABLE_DIR_NAME}.remove-{os.getpid()}-{time.monotonic_ns()}")
    try:
        os.replace(folder, doomed)
    except OSError:
        return
    shutil.rmtree(doomed, ignore_errors=True)


def promote(version: str, *, close_others: bool = False) -> bool:
    """Put ``version`` in ``versions/app``, the one folder Ducky runs from, so a Windows
    allow given once (Controlled folder access, the firewall) holds after every update,
    as with VS Code. The version there becomes ``app.previous`` (the rollback). Only when
    nothing runs from ``app``, unless ``close_others`` (an update the person started: the
    old version has closed and its leftovers are stopped). A source a program runs from
    is copied as hard links, never moved. True when ``app`` holds ``version``; never raises."""
    stable, previous = stable_dir(), previous_dir()
    if folder_version(stable) == version and is_ready(stable):
        return True
    src = next((f for v, f in ready_versions() if v == version and f != stable), None)
    if src is None:
        return False
    if in_use(stable):
        if not close_others:
            return False
        close_processes_in(stable)
        if in_use(stable):
            _log.warning("versions/app is still in use: %s runs from %s", version, src)
            return False
    new = versions_dir() / f"{STABLE_DIR_NAME}.new-{os.getpid()}"
    displaced = folder_version(stable) if is_ready(stable) else ""
    # The running version stays as the rollback, unless it is the one that failed or the
    # rollback itself is what moves in.
    aside = previous if displaced and src != previous and not is_bad(displaced) else None
    try:
        shutil.rmtree(new, ignore_errors=True)
        if in_use(src):
            shutil.copytree(src, new, copy_function=_link_or_copy)
        else:
            os.replace(src, new)
        if folder_version(new) != version:  # an app-<v> folder's marker had no version
            (new / READY_MARKER).unlink(missing_ok=True)  # a hard link: the source's stays
            (new / READY_MARKER).write_text(json.dumps({"version": version}), encoding="utf-8")
        if aside is not None and aside.exists():
            discard(aside)
            if aside.exists():
                aside = None  # a rollback copy still running: the displaced one is dropped
        moved = aside or versions_dir() / f"{STABLE_DIR_NAME}.remove-{os.getpid()}"
        if stable.exists():
            os.replace(stable, moved)
        try:
            os.replace(new, stable)
        except OSError:
            if moved.exists() and not stable.exists():
                os.replace(moved, stable)
            raise
    except OSError:
        _log.warning("versions/app could not take %s", version, exc_info=True)
        if new.exists() and not src.exists():
            os.replace(new, src)  # moved, not copied: put it back
        shutil.rmtree(new, ignore_errors=True)
        return False
    if aside is None:
        shutil.rmtree(moved, ignore_errors=True)
    return True


def is_ready(folder: Path) -> bool:
    return (folder / READY_MARKER).is_file() and (folder / "UEFN-Ducky.exe").is_file()


_SLOT_RANK = {STABLE_DIR_NAME: 0, NEXT_DIR_NAME: 1, PREVIOUS_DIR_NAME: 2}


def ready_versions() -> list[tuple[str, Path]]:
    """Ready version folders, newest first; one per version (app, then app.next, then
    app.previous, then an older ``app-<v>`` folder)."""
    root = versions_dir()
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    found: dict[str, tuple[int, tuple[int, int, int], Path]] = {}
    for entry in entries:
        if entry.name not in _SLOT_RANK and not _DIR_RE.match(entry.name):
            continue
        version = folder_version(entry)
        key = parse_version(version)
        if key is None or not entry.is_dir() or not is_ready(entry):
            continue
        rank = _SLOT_RANK.get(entry.name, 3)
        if version not in found or rank < found[version][0]:
            found[version] = (rank, key, entry)
    ordered = sorted(found.items(), key=lambda item: item[1][1], reverse=True)
    return [(version, folder) for version, (_, _, folder) in ordered]


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data))
        fh.flush()
        os.fsync(fh.fileno())  # current.json must not come back empty after a restart
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


def refresh_root_launcher() -> None:
    """Put this version's Ducky-Launcher.exe in the install folder (shortcuts, the Run key and
    uefn-ducky:// start that one, and updates only ever add version folders): an old root
    launcher keeps its old rules, e.g. 1.2.475 ran every start in the slow Python window.
    Only from a version folder that reached ready; never raises."""
    if not getattr(sys, "frozen", False) or os.environ.get(NO_HOP_ENV) or not running_from_versions():
        return
    root = _installed_exe()
    if root is not None and copy_launcher_files(Path(sys.executable).parent, root.parent):
        from frontend import install_health

        install_health.record("refresh", root.parent)  # Ducky's own change, not one while off


# The launcher and the one DLL it loads. Only files the install already has are replaced.
LAUNCHER_FILES = (NATIVE_LAUNCHER_EXE, "WebView2Loader.dll")


def copy_launcher_files(src_dir: Path, dst_dir: Path) -> list[str]:
    """Replace each of :data:`LAUNCHER_FILES` in ``dst_dir`` that differs from ``src_dir``'s.
    A running (locked) EXE is renamed aside first, Windows allows that, and removed next time.
    Returns the names replaced; a failure leaves that file as it was.

    Every agent host used to run this at once: one shared ``.new`` name let one process
    rename another's half-written copy into place, a root launcher Windows refused ("This
    app can't run on your PC"). So each process copies to its own name, and only a copy
    that reads back identical to a source starting with MZ is swapped in."""
    done: list[str] = []
    for name in LAUNCHER_FILES:
        src, dst = src_dir / name, dst_dir / name
        old = dst.with_name(name + ".old")
        tmp = dst.with_name(f"{name}.{os.getpid()}.new")
        try:
            old.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            if not src.is_file() or not dst.is_file():
                continue
            data = src.read_bytes()
            if name.endswith(".exe") and data[:2] != b"MZ":
                _log.warning("not refreshing %s: %s is not a Windows program", name, src)
                continue
            if len(data) == dst.stat().st_size and data == dst.read_bytes():
                continue
            shutil.copy2(src, tmp)
            # A restart right after the rename kept the new name but not its bytes (an EXE
            # of zeros: "This app can't run on your PC"), so the data is on disk first.
            with open(tmp, "rb+") as fh:
                os.fsync(fh.fileno())
            if tmp.read_bytes() != data:
                raise OSError(f"{tmp.name} does not match {src}")
            try:
                os.replace(tmp, dst)
            except PermissionError:
                os.replace(dst, old)
                os.replace(tmp, dst)
            done.append(name)
        except OSError:
            _log.warning("could not refresh %s in %s", name, dst_dir, exc_info=True)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    return done


def mark_ready(version: str) -> None:
    """The panel of ``version`` came up: its failed-start count goes back to zero, and the
    install's root launcher becomes this version's (:func:`refresh_root_launcher`).
    An AppImage swap is confirmed later, at page_started (session_services._mark_ready)."""
    write_current(version)
    try:
        refresh_root_launcher()
    except Exception:
        _log.warning("root launcher refresh failed", exc_info=True)
    try:
        from frontend import install_health

        install_health.after_ready()
    except Exception:
        pass
    clear_starts(version)


# --- Linux AppImage self-update (frontend.updater) ------------------------------------
APPIMAGE_SWAP_FILE = "appimage_swap.json"


def appimage_swap_paths() -> tuple[Path, Path, Path]:
    """(state, ok mark, failure report) of the last AppImage swap."""
    state = versions_dir() / APPIMAGE_SWAP_FILE
    return state, state.with_suffix(".ok"), state.with_suffix(".failed")


def begin_appimage_swap(version: str, *, from_version: str, app: Path, previous: Path) -> None:
    state, ok, failed = appimage_swap_paths()
    for mark in (ok, failed):
        try:
            mark.unlink(missing_ok=True)
        except OSError:
            pass
    _write_json(state, {"version": version, "from": from_version, "app": str(app),
                        "previous": str(previous), "at": time.time()})


def confirm_appimage_swap(version: str) -> bool:
    """The swapped-in AppImage's page started: its watchdog stops waiting."""
    state, ok, _failed = appimage_swap_paths()
    try:
        if json.loads(state.read_text(encoding="utf-8")).get("version") != version:
            return False
        ok.write_text(str(time.time()), encoding="utf-8")
        return True
    except (OSError, ValueError, AttributeError):
        return False


def appimage_swap_failure() -> dict[str, Any] | None:
    """``{from, to, at}`` when the last AppImage update didn't start and was rolled back
    ("Update failed, back on <to>")."""
    try:
        data = json.loads(appimage_swap_paths()[2].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def clear_starts(version: str) -> None:
    """``version`` started fine: its failed-start count goes back to zero. Nothing else, so
    agent hosts (one per agent turn, maybe an older version's) never repoint current.json
    or rewrite the root launcher."""
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
    if not raw and running_from_versions():
        # Started straight from a version folder (a Run key pinned there, or a 1.2.472
        # launcher hop that passed no env): the version belongs to the install, so that
        # is the start. Else it called itself portable and never updated again.
        exe = _installed_exe() or exe
    if exe.name.lower() == NATIVE_LAUNCHER_EXE.lower():
        return exe.with_name(APP_EXE)
    return exe


def apart_from_the_install() -> bool:
    """This copy is not the PC's install: a test copy (``DUCKY_SANDBOX``), or one on another
    profile folder (a sandbox with its own LOCALAPPDATA). Its version folders are its own,
    while the registry still names the person's real install: it must never start that
    install or write into it (a sandboxed copy that updated itself would have put its own
    launcher in the real install folder)."""
    if os.environ.get("DUCKY_SANDBOX"):
        return True
    try:
        from frontend.app_paths import foreign_profile_reason

        return bool(foreign_profile_reason())
    except Exception:
        return False


def _installed_exe() -> Path | None:
    """The registered install's UEFN-Ducky.exe, when there is one on disk and this copy
    belongs to it (:func:`apart_from_the_install`)."""
    if apart_from_the_install():
        return None
    try:
        from frontend.install_info import installed_location

        location = installed_location()
    except Exception:
        return None
    exe = Path(location) / APP_EXE if location else None
    return exe if exe is not None and exe.is_file() else None


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
    # The newer version picks its own window. An older install's root launcher sets
    # UEFN_DUCKY_NO_NATIVE under its own rules (1.2.475: auto = Python window); passed on, it
    # kept every updated Ducky off the fast launcher. No loop: the newer version's own
    # launcher falls back to its sibling EXE, which is the newest and hands off nowhere.
    env.pop("UEFN_DUCKY_NO_NATIVE", None)
    # This start's no-GPU drawing (busy_pc: the card was full when it started) is not the next
    # one's: passed on, the launcher kept it (it leaves a set variable alone) and every restart
    # drew without the card for good (Nov 18 2026: a 3D panel at 14 fps).
    from frontend.busy_pc import without_software

    without_software(env)
    return env


# What Ducky-Launcher.exe sets for its own backend (native_switch.NATIVE_CHILD_ENV /
# BACKEND_EXE_ENV, headless.BACKEND_UI_PORT_ENV). A version started on its own must not
# inherit them: after a switch under the launcher the new version took itself for a
# launcher's backend, never started its own launcher and ran the slow Python window.
LAUNCHER_CHILD_ENV = ("UEFN_DUCKY_NATIVE_CHILD", "UEFN_DUCKY_BACKEND_EXE", "UEFN_DUCKY_BACKEND_UI_PORT")


def own_start_env() -> dict[str, str]:
    """The environment for a Ducky that starts on its own (a version switch, a restart, a
    rollback): :func:`hand_off_env` without what a launcher gave this process as its backend."""
    env = hand_off_env()
    for name in LAUNCHER_CHILD_ENV:
        env.pop(name, None)
    return env


def drop_inherited_launcher_marks(argv: list[str] | None = None) -> bool:
    """This process's own environment, for a start that is no launcher's backend: only
    ``--backend`` is the launcher's child, so any other start that still carries the
    launcher's marks inherited them (1.2.494 and 1.2.495 pass their whole environment to
    the version they switch to). True when something was dropped."""
    if BACKEND_ARG in (sys.argv if argv is None else argv)[1:]:
        return False
    return bool([name for name in LAUNCHER_CHILD_ENV if os.environ.pop(name, None) is not None])


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


def kill_with_me(proc: subprocess.Popen) -> None:
    """End ``proc`` when this process ends, however it ends (the job above). For the tool
    server frontend.shared_mcp_adapter starts for an agent whose host is not answering."""
    _kill_with_me(proc)


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


def start_dir(exe: Path) -> Path:
    """The working folder for a started Ducky: never inside a version folder, which
    Windows then refuses to rename (:func:`promote`)."""
    try:
        if exe.resolve().is_relative_to(versions_dir().resolve()):
            return versions_dir().parent
    except (OSError, ValueError):
        pass
    return exe.parent


def start_detached(exe: Path, args: list[str], *, env: dict[str, str] | None = None) -> bool:
    """Start ``exe`` on its own, outliving this process (and any job this one is in)."""
    flags = 0
    if sys.platform == "win32":
        flags = _CREATE_NEW_PROCESS_GROUP | _DETACHED_PROCESS
    for extra in (_CREATE_BREAKAWAY_FROM_JOB, 0) if sys.platform == "win32" else (0,):
        try:
            subprocess.Popen(
                [str(exe), *args],
                env=env or own_start_env(),
                cwd=str(start_dir(exe)),
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
    if not bridge and not backend_mode() and _restart_from_stable(current):
        os._exit(0)
    for _ in range(4):
        target = newest_usable(current)
        if target is None:
            break
        version, folder = target
        if not (folder / Path(sys.executable).name).is_file():
            break
        if not bridge and not record_start(version):
            continue  # failed to start before: try the next one down
        if bridge:
            # Runs from app like everything else; an update closes it, and the IDE starts
            # it again on its next tool call.
            write_current(version, installed)
            raise SystemExit(run_attached(folder / Path(sys.executable).name, sys.argv[1:]))
        promote(version)  # only when nothing runs from app: a start never stops a running Ducky
        exe = version_dir(version) / Path(sys.executable).name
        write_current(version, installed)
        if backend_mode():
            # Ducky-Launcher.exe talks to us over stdin/stdout: the newer version must
            # keep those pipes, so it runs attached instead of detached.
            raise SystemExit(run_attached(exe, sys.argv[1:]))
        if start_detached(exe, _detached_args(sys.argv[1:])):
            os._exit(0)
        break
    write_current(current, installed)  # this EXE runs


def _restart_from_stable(current: str) -> bool:
    """A window start outside ``versions/app`` (an update starts the new version in
    app.next, a rollback starts app.previous, 1.2.529 and older switch to app-<v>): put
    this version in ``versions/app`` and start it from there. After an update or rollback
    (``--after-update`` waited for the old one) whatever still runs from app is closed
    first, as VS Code's updater does. True when that start is on its way and this process
    should leave."""
    here = Path(sys.executable)
    if not running_from_versions() or here.parent.name == STABLE_DIR_NAME:
        return False
    if folder_version(here.parent) != current:
        return False
    if not promote(current, close_others=_after_update):
        return False
    return start_detached(stable_dir() / here.name, _detached_args(sys.argv[1:]))


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
    """A downloaded version crashed while starting: mark it bad and start the version
    before it (app.previous, which moves back into app), else the EXE that was launched,
    which hands off to the next version down. True when the fallback started.

    A rollback never starts Ducky-Launcher.exe. As its backend, this only marks the
    version bad: the launcher then exits with an error and the UEFN-Ducky.exe that
    started it falls back on its own, so there is one window, not two."""
    if not running_from_versions():
        return False
    mark_bad(current, reason)
    repoint_to_installed_pick()
    if backend_mode():
        return False
    # --after-update: the EXE waits for this process to end before it starts (every version
    # reads it). Started at once, it saw this pid alive in boot.json and turned its own crash
    # record off for the whole session.
    wait_for_me = _detached_args([AFTER_UPDATE_ARG, str(os.getpid())])
    previous = previous_dir()
    before = folder_version(previous)
    if (
        before
        and before != current
        and is_ready(previous)
        and not is_bad(before)
        and Path(sys.executable).parent != previous
        and record_start(before)
        and start_detached(previous / APP_EXE, wait_for_me)
    ):
        return True
    exe = launcher_exe()
    if not exe.is_file():
        return False
    return start_detached(exe, wait_for_me)


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
    global _after_update
    _after_update = True
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
