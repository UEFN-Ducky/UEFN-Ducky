"""Silent in-app update via the Inno Setup installer. Installed channel only."""

from __future__ import annotations

import csv
import hashlib
import http.client
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request  # noqa: F401  (tests patch updater.urllib.request.urlopen)
from pathlib import Path
from typing import Any

from frontend import __version__
from frontend.install_info import get_install_info
from frontend.version_check import get_app_update_status, is_remote_newer, parse_version_tuple

# Delay before we reap ourselves so apply_update() / launch_uninstall() can
# return to the UI. Keep this short: Setup is already running and will stall on
# Restart Manager if the EXE stays locked.
_SHUTDOWN_DELAY_S = 0.15

# After the unelevated Setup stub exits (UAC Yes → elevated child), wait this
# long before deciding no child appeared (UAC No / launch failed).
_ELEVATION_HANDOFF_S = 2.0

# Fresh downloads often lose the first CreateProcess to MOTW / Defender. The
# user's second "Check for updates" click is this retry against the cached EXE.
_LAUNCH_RETRY_S = 1.25

# Inno silent upgrade: no wizard, force-close anything still holding the install
# dir (bridges / a raced panel), never trigger a Windows restart. Relaunch is
# ONLY CurStepChanged(ssDone) + LaunchApp — never relaunch from here on failure.
# Do NOT use /NOCLOSEAPPLICATIONS: elevated Setup fails when the panel EXE stays
# locked (UAC Yes → "Installer did not finish" with a cached download).
_SILENT_INSTALL_ARGS = (
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/CLOSEAPPLICATIONS",
    "/FORCECLOSEAPPLICATIONS",
)

_INSTALL_DECLINED = (
    "Installer did not finish. If Windows asked for permission, choose Yes and "
    "try again — the download is already cached."
)

_progress_lock = threading.Lock()
_progress: dict[str, Any] = {
    "stage": "idle",
    "downloaded_bytes": 0,
    "total_bytes": 0,
    "error": None,
}
_cancel = threading.Event()
_CANCELLED = "Update cancelled."
# Active download response — closed by cancel_update() to unblock a stuck read.
_active_download_lock = threading.Lock()
_active_download_resp: Any | None = None


def _set_progress(
    *,
    stage: str | None = None,
    downloaded_bytes: int | None = None,
    total_bytes: int | None = None,
    error: str | None | object = ...,
) -> None:
    with _progress_lock:
        if stage is not None:
            _progress["stage"] = stage
        if downloaded_bytes is not None:
            _progress["downloaded_bytes"] = int(downloaded_bytes)
        if total_bytes is not None:
            _progress["total_bytes"] = int(total_bytes)
        if error is not ...:
            _progress["error"] = error


def get_update_progress() -> dict[str, Any]:
    """Snapshot of the in-flight apply_update() progress (safe to poll from UI)."""
    with _progress_lock:
        return dict(_progress)


def cancel_update() -> dict[str, Any]:
    """Request cancel of an in-flight download/verify (safe to call from the UI thread)."""
    _cancel.set()
    with _active_download_lock:
        resp = _active_download_resp
    if resp is not None:
        try:
            resp.close()
        except Exception:
            pass
    return {"ok": True}


def _cancelled() -> bool:
    return _cancel.is_set()


def _silent_install_args(install_scope: Any) -> list[str]:
    args = list(_SILENT_INSTALL_ARGS)
    # Silent runs never show the per-user/per-machine dialog and would default to
    # per-user — pass the original scope so a per-machine install updates in place
    # (triggers one UAC prompt) instead of creating a second per-user copy.
    if install_scope == "machine":
        args.append("/ALLUSERS")
    else:
        args.append("/CURRENTUSER")
    return args


def _setup_log_args() -> list[str]:
    """``/LOG=…\\logs\\setup-<ts>.log``: every Setup run leaves a log (install_health reads it)."""
    try:
        from frontend.install_health import setup_log_args

        return setup_log_args()
    except Exception:
        return []


def _result(*, ok: bool, error: str | None, stage: str) -> dict[str, Any]:
    _set_progress(stage=stage, error=error)
    return {"ok": ok, "error": error, "stage": stage}


def installer_url_allowed(url: str) -> bool:
    """HTTPS anywhere; plain http only on this machine (local update rehearsal)."""
    from urllib.parse import urlparse

    text = (url or "").strip()
    scheme = urlparse(text).scheme.lower()
    if scheme == "https":
        return True
    if scheme == "http":
        host = (urlparse(text).hostname or "").lower()
        return host in ("127.0.0.1", "localhost", "::1")
    return False


def installer_cache_dir() -> Path:
    return Path(tempfile.gettempdir()) / "UEFN-Ducky"


def _installer_download_path(remote_version: str) -> Path:
    # The basename must NOT start with "UEFN-Ducky": the shutdown sweep in
    # frozen_process kills every UEFN-Ducky* process and would reap the
    # running installer.
    safe_version = "".join(c for c in remote_version if c.isalnum() or c in ".-_") or "latest"
    return installer_cache_dir() / f"Setup-{safe_version}.exe"


def _unlink_quiet(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def remove_installer_file(path: Path) -> bool:
    """Delete one cached Setup EXE after a successful install (best-effort)."""
    return _unlink_quiet(path)


def sweep_installer_cache(*, keep_newer_than: str | None = None) -> int:
    """Remove stale ``%TEMP%/UEFN-Ducky/Setup-*.exe`` files.

    Keeps only installers **newer** than ``keep_newer_than`` (default: running
    app version) so a declined UAC prompt can reuse the download. After a
    successful upgrade the new app version matches the Setup file, so it is
    removed on next startup.
    """
    if keep_newer_than is None:
        # Startup sweep of the (new) app. Not the old app's own sweep after Setup in
        # apply_update: that process is the old version and is about to exit.
        stop_old_bridges_after_update()
        stop_replaced_launchers()
    cache_dir = installer_cache_dir()
    if not cache_dir.is_dir():
        return 0
    floor = (keep_newer_than or __version__).strip() or __version__
    removed = 0
    for path in cache_dir.glob("Setup-*.exe"):
        ver = path.stem[len("Setup-") :] if path.stem.startswith("Setup-") else ""
        # Pending update for a version we do not have yet — keep the cache.
        if ver and parse_version_tuple(ver) is not None and is_remote_newer(floor, ver):
            continue
        if _unlink_quiet(path):
            removed += 1
    sweep_setup_host_leftovers()
    return removed


def stop_old_bridges_after_update() -> list[int]:
    """The newly installed app stops tool servers (bridges) from the old install.

    An IDE can keep an old ``UEFN-Ducky*.exe`` bridge running across the update (a
    versioned EXE Setup never had to replace) and it keeps connecting to the listener.
    Records this app as the running version, then stops every older bridge. Returns
    the stopped pids; never raises.
    """
    try:
        from backend.bridge import bridge_version

        if not bridge_version.registry_active():
            return []
        bridge_version.note_running_app(__version__)
        return bridge_version.stop_old_bridges(version=__version__)
    except Exception:
        return []


# The native window host. Its name does not start with UEFN-Ducky, so the
# kill_uefn_ducky_processes sweep (and old versions' sweeps) never reach it: a
# launcher closes by itself when its backend's pipe closes.
LAUNCHER_EXE_NAME = "Ducky-Launcher.exe"
# How long a replaced version's launcher gets to close on its own before it is stopped.
LAUNCHER_GRACE_S = 5.0


def replaced_launcher_pids(
    processes: list[tuple[int, str]],
    *,
    current_version: str,
    versions_root: Path,
    keep_pids: set[int],
) -> list[int]:
    """Pids of Ducky-Launcher.exe copies that belong to a replaced version.

    Only a launcher whose EXE sits in ``versions\\app-<older than current_version>``
    counts. The installed copy (Setup replaced it in place, so a running one is the
    current version's), this version's own folder, any other folder and ``keep_pids``
    (the launcher hosting this process) are never targeted."""
    floor = parse_version_tuple(current_version)
    found: list[int] = []
    for pid, exe in processes:
        if not pid or pid in keep_pids or not exe:
            continue
        path = Path(exe)
        if path.name.lower() != LAUNCHER_EXE_NAME.lower():
            continue
        folder = path.parent
        try:
            if folder.parent.resolve() != versions_root.resolve():
                continue
        except OSError:
            continue
        if not folder.name.startswith("app-"):
            continue
        owner = parse_version_tuple(folder.name[4:])
        if owner is None or floor is None or owner >= floor:
            continue
        found.append(int(pid))
    return found


def _launcher_processes() -> list[tuple[int, str]]:
    """(pid, full EXE path) of every running Ducky-Launcher.exe; [] when unknown."""
    if sys.platform != "win32":
        return []
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        completed = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {LAUNCHER_EXE_NAME}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True, errors="replace",
            creationflags=flags,
            timeout=10.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    pids: list[int] = []
    for row in csv.reader((completed.stdout or "").splitlines()):
        if len(row) > 1 and row[0].strip().lower() == LAUNCHER_EXE_NAME.lower():
            try:
                pids.append(int(row[1]))
            except ValueError:
                continue
    return [(pid, path) for pid in pids if (path := _process_image_path(pid))]


def _process_image_path(pid: int) -> str:
    try:
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
    except (OSError, AttributeError, ValueError):
        pass
    return ""


def _pid_alive(pid: int) -> bool:
    return any(p == pid for p, _ in _launcher_processes())


def _terminate_pid(pid: int) -> None:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.run(["taskkill", "/PID", str(int(pid))], capture_output=True, creationflags=flags, timeout=10.0)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _own_launcher_pids() -> set[int]:
    keep = {os.getpid()}
    try:
        keep.add(os.getppid())
    except (OSError, AttributeError):
        pass
    return keep


def stop_replaced_launchers(
    *,
    list_processes: Any = None,
    is_alive: Any = None,
    terminate: Any = None,
    grace_s: float = LAUNCHER_GRACE_S,
    sleep: Any = time.sleep,
) -> list[int]:
    """Stop launchers left by a replaced version, only after they had time to close.

    A launcher closes by itself once its backend is gone (stdin EOF). Those still
    running after ``grace_s`` are stopped; nothing else named Ducky-Launcher.exe is
    touched. Returns the stopped pids; never raises."""
    try:
        from frontend import app_versions

        listing = list_processes or _launcher_processes
        alive = is_alive or _pid_alive
        kill = terminate or _terminate_pid
        targets = replaced_launcher_pids(
            list(listing()),
            current_version=__version__,
            versions_root=app_versions.versions_dir(),
            keep_pids=_own_launcher_pids(),
        )
        if not targets:
            return []
        deadline = time.monotonic() + max(0.0, float(grace_s))
        waiting = list(targets)
        while waiting and time.monotonic() < deadline:
            waiting = [pid for pid in waiting if alive(pid)]
            if waiting:
                sleep(0.25)
        stopped: list[int] = []
        for pid in waiting:
            if alive(pid):
                kill(pid)
                stopped.append(pid)
        return stopped
    except Exception:
        return []


# What the Ducky Setup host and its engine leave in %LOCALAPPDATA%/UEFN-Ducky.
# setup-engine/ holds a ~180 MB copy of the installer that nothing else removes.
_SETUP_HOST_LEFTOVERS = ("setup-engine", "setup-ui", "setup-progress.txt")
_SETUP_HOST_LEFTOVER_GLOBS = ("setup-webview-*",)


def _setup_host_active(names: set[str] | None) -> bool:
    """True while any installer is running (or when we cannot tell)."""
    if names is None:
        return sys.platform == "win32"
    for name in names:
        if name == SETUP_ENGINE_EXE.lower() or name.startswith("uefn-ducky-setup"):
            return True
        if name.startswith("setup-") and name.endswith(".exe") and parse_version_tuple(name[6:-4]):
            return True
    return False


def sweep_setup_host_leftovers(app_root: Path | None = None) -> int:
    """Remove the Setup host's extracted engine, UI and progress file.

    Skipped while any Setup is running, so an install in progress never loses
    its engine. Returns how many top-level entries were removed.
    """
    if app_root is None:
        from frontend.app_paths import resolve_app_data_dir

        app_root = resolve_app_data_dir()
    if not app_root.is_dir():
        return 0
    targets = [app_root / name for name in _SETUP_HOST_LEFTOVERS]
    for pattern in _SETUP_HOST_LEFTOVER_GLOBS:
        targets.extend(app_root.glob(pattern))
    targets = [path for path in targets if path.exists()]
    # Listing processes costs a tasklist run: only pay it when there is something to remove.
    if not targets or _setup_host_active(_running_image_names()):
        return 0
    removed = 0
    for path in targets:
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.is_file():
                path.unlink()
            else:
                continue
        except OSError:
            continue
        if not path.exists():
            removed += 1
    return removed


def _cached_installer_usable(dest: Path, expected_sha256: str | None) -> bool:
    """True when a prior download is complete and matches the feed digest."""
    if not expected_sha256:
        return False
    try:
        if not dest.is_file() or dest.stat().st_size <= 0:
            return False
    except OSError:
        return False
    return _verify_sha256(dest, expected_sha256) is None


# The signed Ducky Setup host runs the Inno installer as this file, extracted to
# %LOCALAPPDATA%/UEFN-Ducky/setup-engine/. On an all-users update the engine asks
# for UAC, starts an elevated copy of itself and exits. The host waits for that
# elevated copy and returns its exit code; a host that is gone early (killed, or
# a build that did not wait) still leaves Setup-engine.exe installing, so both
# image names count as "Setup is running".
SETUP_ENGINE_EXE = "Setup-engine.exe"

# The host creates this named event once UAC was accepted and the elevated
# engine runs (release/installer/host/Engine.cs HandoffEventName). It lets a
# machine-scope update close the panel then, as it does when the plain Inno
# Setup stub exits after UAC, instead of waiting for Restart Manager to close it.
_HANDOFF_EVENT = "Local\\UEFN-Ducky-Setup-Handoff-{pid}"
_HANDOFF_POLL_S = 0.2
_SYNCHRONIZE = 0x00100000


def _host_handoff_signalled(pid: int) -> bool:
    """True once the Setup host with this PID reports the elevated install started."""
    if sys.platform != "win32" or not pid:
        return False
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenEventW.restype = ctypes.c_void_p
        kernel32.OpenEventW.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p)
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        handle = kernel32.OpenEventW(_SYNCHRONIZE, 0, _HANDOFF_EVENT.format(pid=int(pid)))
    except (OSError, AttributeError, ValueError):
        return False
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def _running_image_names() -> set[str] | None:
    """Lower-cased image names of every process (``None`` when tasklist fails)."""
    if sys.platform != "win32":
        return None
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        completed = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True, errors="replace",
            creationflags=flags,
            timeout=10.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    names: set[str] = set()
    for row in csv.reader((completed.stdout or "").splitlines()):
        if row:
            names.add(row[0].strip().lower())
    return names


def _installer_process_running(dest: Path) -> bool:
    """True while Setup (``dest``'s image) or the host's Inno engine is alive."""
    if not dest.name:
        return False
    names = _running_image_names()
    if not names:
        return False
    return dest.name.lower() in names or SETUP_ENGINE_EXE.lower() in names


def _setup_still_running_after_wait(dest: Path) -> bool:
    """True when Setup is still alive after the Popen handle exited (UAC elevation)."""
    deadline = time.monotonic() + _ELEVATION_HANDOFF_S
    while time.monotonic() < deadline:
        if _installer_process_running(dest):
            return True
        time.sleep(0.15)
    return _installer_process_running(dest)


def _unblock_downloaded_exe(path: Path) -> None:
    """Drop the Mark-of-the-Web ADS so the first launch is not SmartScreen-blocked."""
    try:
        os.remove(f"{path}:Zone.Identifier")
    except OSError:
        pass


def _wait_exe_unlocked(path: Path, *, timeout: float = 4.0) -> None:
    """Wait until another process (Defender) releases the just-written Setup EXE."""
    flags = os.O_RDWR | getattr(os, "O_BINARY", 0)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(str(path), flags)
            os.close(fd)
            return
        except OSError:
            if time.monotonic() >= deadline:
                return
            time.sleep(0.2)


def _prepare_installer_exe(path: Path) -> None:
    """Flush + unblock a just-downloaded Setup so the first launch can run."""
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_BINARY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass
    _unblock_downloaded_exe(path)
    _wait_exe_unlocked(path)


# CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB.
# Setup must outlive FORCECLOSE / our os._exit. taskkill /T on the panel used to
# reap the installer (download finishes, Ducky closes, nothing installs).
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def _popen_setup(dest: Path, args: list[str]) -> subprocess.Popen[Any]:
    cmd = [str(dest), *args]
    if sys.platform != "win32":
        return subprocess.Popen(cmd, close_fds=True)
    flag_sets = (
        _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP | _CREATE_BREAKAWAY_FROM_JOB,
        _CREATE_NO_WINDOW | _CREATE_BREAKAWAY_FROM_JOB,
        _CREATE_NO_WINDOW,
        0,
    )
    last_err: OSError | None = None
    for flags in flag_sets:
        try:
            return subprocess.Popen(cmd, close_fds=True, creationflags=flags)
        except OSError as exc:
            last_err = exc
    if last_err is not None:
        raise last_err
    return subprocess.Popen(cmd, close_fds=True)


def _wait_setup_exit(proc: subprocess.Popen[Any]) -> int | None:
    """Setup's exit code, or ``None`` as soon as the host reports the UAC hand-off.

    The Ducky Setup host stays until the elevated engine finishes, so without
    the hand-off signal a machine-scope update would sit here until Restart
    Manager closes the panel. The plain Inno stub never signals; it is handled
    by its exit plus ``_setup_still_running_after_wait`` as before.
    """
    pid = int(getattr(proc, "pid", 0) or 0)
    try:
        while True:
            code = proc.poll()
            if code is not None:
                return int(code)
            if _host_handoff_signalled(pid):
                return None
            time.sleep(_HANDOFF_POLL_S)
    except OSError:
        return 1


def _launch_setup_until_handoff(
    dest: Path, args: list[str], *, wait_for_elevation: bool = True
) -> tuple[int, bool]:
    """Launch Setup; retry once if the stub dies before an elevated child appears.

    Per-user installs: do not wait() for Setup to finish — FORCECLOSE would kill
    this panel and (without breakaway) the installer with it. Machine installs
    wait for the unelevated stub to hand off to the elevated child: the plain
    Inno stub exits, the Ducky Setup host signals ``_HANDOFF_EVENT``.
    """
    last_code = 1
    for attempt in range(2):
        if attempt:
            time.sleep(_LAUNCH_RETRY_S)
        try:
            proc = _popen_setup(dest, args)
        except OSError:
            continue
        if not wait_for_elevation:
            time.sleep(0.25)
            if proc.poll() is None or _installer_process_running(dest):
                return 0, True
            last_code = int(proc.poll() if proc.poll() is not None else 1)
            continue
        code = _wait_setup_exit(proc)
        if code is None:
            return 0, True
        last_code = code
        if _setup_still_running_after_wait(dest):
            return last_code, True
        if last_code == 0:
            return last_code, False
    return last_code, False


def _download(url: str, dest: Path, *, timeout: float = 120.0) -> str | None:
    """Retry interrupted transfers, resuming from the bytes already on disk; expose a
    complete file only after an atomic move."""
    from frontend.update_network import (ATTEMPTS, MAX_TRIES, host_order, host_worked, note_download_fail,
                                         retryable, retry_delay)

    partial = dest.with_suffix(dest.suffix + ".part")
    _unlink_quiet(partial)
    misses = 0
    # The usual link, then the same file on storage's second host when this one's
    # connection keeps failing (the bytes already on disk are kept).
    links = host_order(url)
    try:
        for _ in range(MAX_TRIES):
            if _cancelled():
                return _CANCELLED
            before = partial.stat().st_size if partial.exists() else 0
            try:
                _download_once(links[0], partial, timeout=timeout)
                if _cancelled():
                    return _CANCELLED
                os.replace(partial, dest)
                if links[0] != url or len(links) > 1:
                    host_worked(url, links[0])
                return None
            except (OSError, ValueError, http.client.HTTPException) as exc:
                if _cancelled():
                    return _CANCELLED
                # A try that got new bytes doesn't count: a link that keeps dropping
                # still finishes, a dead one stops after ATTEMPTS empty tries.
                got = partial.stat().st_size if partial.exists() else 0
                misses = 0 if got > before else misses + 1
                if misses >= ATTEMPTS and retryable(exc) and len(links) > 1:
                    note_download_fail(exc, url=url, tried=links[0], what="setup")
                    links, misses = links[1:], 0
                    continue
                if misses >= ATTEMPTS or not retryable(exc):
                    return _download_error(exc)
                if _cancel.wait(retry_delay(misses)):
                    return _CANCELLED
        return "Update download failed."
    finally:
        _unlink_quiet(partial)


def _download_error(exc: BaseException) -> str:
    """A Setup download that gave up, in words a person can act on. It read
    "<urlopen error [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of
    protocol (_ssl.c:1032)>" and nothing else (Nov 13 2026)."""
    try:
        import urllib.error

        from frontend.net_access import explain
        from frontend.version_check import download_page_url

        browser = f"You can also get the installer in your browser: {download_page_url()}"
        if isinstance(exc, urllib.error.HTTPError):
            return f"The download server answered with an error (HTTP {exc.code}). Try again in a minute. {browser}"
        return f"{explain(exc, 'the download server')[1]} {browser}"
    except Exception:  # noqa: BLE001 — the raw error still says something
        return str(exc)


def _track_response(resp: Any | None) -> None:
    global _active_download_resp
    with _active_download_lock:
        _active_download_resp = resp


def _download_once(url: str, dest: Path, *, timeout: float) -> None:
    from frontend.update_network import fetch_into

    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("ab") as out:
        _set_progress(stage="download", downloaded_bytes=out.tell(), error=None)
        fetch_into(
            url,
            out,
            headers={"User-Agent": f"UEFN-Ducky/{__version__}"},
            timeout=timeout,
            on_chunk=lambda done, total: _set_progress(downloaded_bytes=done, total_bytes=total),
            cancelled=_cancelled,
            on_open=_track_response,
            content_checked=True,  # the installer must match the feed's installerSha256
        )
        out.flush()
        try:
            os.fsync(out.fileno())
        except OSError:
            pass


def _verify_sha256(path: Path, expected_hex: str) -> str | None:
    """Compare the file digest to ``expected_hex``. Returns an error string or ``None``."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        return str(exc)
    actual = digest.hexdigest().lower()
    if actual != expected_hex.strip().lower():
        return f"SHA256 mismatch: expected {expected_hex}, got {actual}"
    return None


def _mark_closed() -> None:
    """Exiting for the installer is on purpose: the next launch must not report a crash."""
    try:
        from frontend import boot_report

        boot_report.mark("closed")
    except Exception:
        pass


def _plugin_loads_end() -> None:
    """The update ends this Ducky (itself in a moment, or Setup before that): a plugin load
    still running is no crash for the next start to find."""
    try:
        from frontend import boot_report

        boot_report._end_plugin_loads()
    except Exception:
        pass


def _shutdown_after_delay() -> None:
    """Reap the panel and IDE bridge workers so the installer can replace files."""
    _plugin_loads_end()

    def _shutdown() -> None:
        try:
            from frontend.frozen_process import kill_uefn_ducky_processes, release_panel_process

            # Clear panel.pid before exit so the relaunched EXE does not wait on
            # a dead/dying PID handoff. Do not include_self: Stop-Process / taskkill /T
            # reaps Setup when it is still in this job.
            release_panel_process()
            kill_uefn_ducky_processes(include_self=False)
        except Exception:
            pass
        _mark_closed()
        # Root cause of stuck "Updating…" lock: PowerShell/taskkill can fail and
        # leave the panel alive with Cancel disabled. Always die so Setup can
        # replace the EXE and the overlay cannot strand the user.
        os._exit(0)

    threading.Timer(_SHUTDOWN_DELAY_S, _shutdown).start()


def _exit_self_after_delay() -> None:
    """Leave after a successful Setup wait without killing a relaunched panel."""
    _plugin_loads_end()

    def _exit() -> None:
        try:
            from frontend.frozen_process import release_panel_process

            release_panel_process()
        except Exception:
            pass
        _mark_closed()
        os._exit(0)

    threading.Timer(_SHUTDOWN_DELAY_S, _exit).start()


def _stop_all_agents() -> None:
    """Cancel every running agent so the installer can replace the EXE cleanly."""
    _set_progress(stage="stopping_agents", error=None)
    try:
        from frontend.ui_web.agent_turns.state import cancel_agent

        cancel_agent()
    except Exception:
        # ponytail: best-effort — update must proceed even if agent cancel races.
        pass


def apply_update() -> dict[str, Any]:
    """Download or reuse cached Setup, launch silently, exit only once install proceeds.

    Declining the Windows UAC prompt must leave the panel running with the
    cached installer. Relaunch happens only after a successful install via Inno
    ``LaunchApp`` at ssDone — never from a failed Setup.
    """
    _cancel.clear()
    _set_progress(stage="check", downloaded_bytes=0, total_bytes=0, error=None)
    if sys.platform.startswith("linux"):
        return apply_linux_update()
    status = get_app_update_status()
    channel = str(status.get("channel") or "")
    if channel == "dev":
        return _result(
            ok=False,
            error="Dev builds cannot self-update — only production installs update from the Store.",
            stage="check",
        )
    if not status["installed"] or channel != "installed":
        return _result(
            ok=False,
            error="Only an installed production copy can update from the Store.",
            stage="check",
        )
    if not status["update_available"]:
        return _result(ok=False, error="No update available.", stage="check")

    url = status["installer_url"]
    if not url:
        return _result(ok=False, error="Update feed has no installerUrl.", stage="check")
    if not installer_url_allowed(url):
        return _result(ok=False, error=f"Refusing non-HTTPS installer URL: {url}", stage="check")
    sha256 = status["installer_sha256"]
    if not sha256:
        return _result(
            ok=False,
            error="Update feed is missing installerSha256 — refusing to install.",
            stage="check",
        )

    _stop_all_agents()
    if _cancelled():
        return _result(ok=False, error=_CANCELLED, stage="stopping_agents")

    dest = _installer_download_path(str(status["remote_version"]))

    # Reuse a prior download when the feed digest still matches (declined UAC, etc.).
    if _cached_installer_usable(dest, sha256):
        try:
            size = dest.stat().st_size
        except OSError:
            size = 0
        _set_progress(stage="verify", downloaded_bytes=size, total_bytes=size, error=None)
    else:
        # Partial / corrupt cache — wipe before rewrite so we never launch junk.
        _unlink_quiet(dest)
        error = _download(url, dest)
        if error:
            if error == _CANCELLED:
                _unlink_quiet(dest)
            return _result(ok=False, error=error, stage="download")

        if _cancelled():
            _unlink_quiet(dest)
            return _result(ok=False, error=_CANCELLED, stage="download")

        _set_progress(stage="verify", error=None)
        if _cancelled():
            _unlink_quiet(dest)
            return _result(ok=False, error=_CANCELLED, stage="verify")
        error = _verify_sha256(dest, sha256)
        if error:
            _unlink_quiet(dest)
            return _result(ok=False, error=error, stage="verify")

    if _cancelled():
        return _result(ok=False, error=_CANCELLED, stage="verify")

    _prepare_installer_exe(dest)
    _set_progress(stage="launch", error=None)
    # Drop IDE bridge workers before Setup starts so Restart Manager does not
    # wait (or prompt) on locked UEFN-Ducky*.exe handles. Do NOT exit the panel
    # yet — wait for Setup so a declined UAC keeps the app + cached installer.
    try:
        from frontend.frozen_process import kill_uefn_ducky_processes

        kill_uefn_ducky_processes(include_self=False)
    except Exception:
        pass
    _set_progress(stage="installing", error=None)
    code, child_running = _launch_setup_until_handoff(
        dest,
        [*_silent_install_args(status["install_scope"]), *_setup_log_args()],
        wait_for_elevation=status.get("install_scope") == "machine",
    )

    # /ALLUSERS: unelevated stub often exits 0 right after UAC Yes while the
    # elevated Setup-{ver}.exe child is still installing. Do not delete the
    # cache or assume success until that child is gone.
    if child_running:
        # Install underway — unlock the EXE. Inno LaunchApp relaunches on ssDone only.
        _shutdown_after_delay()
        return _result(ok=True, error=None, stage="restarting")

    if code != 0:
        # UAC No / install failed — keep the verified cache for the next attempt.
        return _result(ok=False, error=_INSTALL_DECLINED, stage="installing")

    # Setup finished and we are still alive (FORCECLOSE did not reap us).
    # Exit without killing a panel Inno may already have relaunched via LaunchApp.
    remove_installer_file(dest)
    sweep_installer_cache(keep_newer_than=str(status.get("remote_version") or __version__))
    _exit_self_after_delay()
    return _result(ok=True, error=None, stage="restarting")


def launch_uninstall() -> dict[str, Any]:
    """Open UEFN Ducky's own uninstaller (why, keep or delete data, progress).

    It can still answer "Keep UEFN Ducky", and Inno's uninstaller closes the app when the
    person goes on, so the app stays open. Only a copy without it (an all-users install
    from before it shipped) falls back to the registered uninstall command and exits.
    """
    from frontend.uninstaller_link import EXE_NAME

    info = get_install_info()
    if not info["installed"]:
        return _result(ok=False, error="This copy is portable — nothing to uninstall.", stage="check")
    own = Path(str(info.get("install_location") or "")) / EXE_NAME
    if info.get("install_location") and own.is_file():
        command, own_window = f'"{own}"', True
    else:
        # Never the quiet command first: that is Inno's silent uninstall with no window.
        command, own_window = info["uninstall_command"] or info["quiet_uninstall_command"], False
    if not command:
        return _result(ok=False, error="No uninstall command registered.", stage="check")

    try:
        # UninstallString is a full command line (quoted exe + args); pass it
        # through as-is so CreateProcess parses it the same way Windows does.
        subprocess.Popen(command, close_fds=True)
    except OSError as exc:
        return _result(ok=False, error=str(exc), stage="launch")

    if own_window:
        return _result(ok=True, error=None, stage="opened")
    _shutdown_after_delay()
    return _result(ok=True, error=None, stage="uninstalling")


# --- Linux -----------------------------------------------------------------------------
# Windows never runs anything below: apply_update() only comes here on Linux.

# install_info.install_kind() -> the store record's ``package`` (DuckyOS uds_app_release).
_PACKAGE_FOR_KIND = {
    "installer": "setup", "appimage": "appimage", "deb": "deb", "rpm": "rpm",
    "flatpak": "flatpak", "aur": "aur",
}
_ARCH_NAMES = {"x86_64": "x64", "amd64": "x64", "x64": "x64", "aarch64": "arm64", "arm64": "arm64"}
# What the update button does per install kind (the web banner reads ``update_action``).
UPDATE_ACTIONS = {
    "appimage": "restart", "deb": "install", "rpm": "install", "flatpak": "install",
    "aur": "manual", "installer": "restart",
}
# The release lane's minisign public key (the .pub file's base64 line, key id
# CB88729E70843096); its secret half is the MINISIGN_SECRET_KEY CI secret. Every AppImage
# update must carry a matching .minisig. Empty would mean SHA-256 from the HTTPS feed only.
MINISIGN_PUBLIC_KEY = "RWSWMIRwnnKIy4h1XdDWMtouy9laP5uw5K1gGe3HVNh5DFIWT7woHBvM"
# A swapped-in AppImage whose page hasn't started (boot stage page_started, which
# session_services turns into app_versions.confirm_appimage_swap) by then is rolled back.
HEALTHY_WITHIN_S = 60
# Leftovers of the AppImage process that would break the watchdog or the next AppImage.
_RUNTIME_ENV = ("APPDIR", "APPIMAGE", "ARGV0", "OWD", "PYTHONHOME", "PYTHONPATH", "_MEIPASS2")
_PACKAGE_TIMEOUT_S = 1800.0

# Runs detached from the old AppImage: starts the new one, waits for its ready mark, and
# puts the old file back (then starts it) when the new one exits or never gets ready.
_WATCHDOG_SH = r"""
app=$1 prev=$2 ok=$3 failed=$4 timeout=$5 old=$6 new=$7 pid=$8
"$app" --after-update "$pid" &
child=$!
i=0
while [ "$i" -lt "$timeout" ]; do
  [ -e "$ok" ] && exit 0
  kill -0 "$child" 2>/dev/null || break
  sleep 1
  i=$((i+1))
done
[ -e "$ok" ] && exit 0
kill "$child" 2>/dev/null
mv -f "$prev" "$app" || exit 1
printf '{"from": "%s", "to": "%s", "at": %s}' "$new" "$old" "$(date +%s)" > "$failed"
exec "$app"
"""


def _os_name(plat: str | None = None) -> str:
    plat = plat or sys.platform
    if plat == "win32":
        return "windows"
    if plat.startswith("linux"):
        return "linux"
    return "macos" if plat == "darwin" else plat


def _install_kind() -> str:
    from frontend.install_info import install_kind

    return install_kind()


def pick_asset(
    payload: dict[str, Any],
    *,
    plat: str | None = None,
    machine: str | None = None,
    kind: str | None = None,
) -> dict[str, Any] | None:
    """The feed's ``assets`` row for this OS, CPU and install kind, e.g. an AppImage on
    aarch64 -> ``linux/arm64/appimage``. Windows falls back to the old top-level
    installerUrl / installerSha256 (records from before ``assets``, always x64 Setup)."""
    import platform

    os_name = _os_name(plat)
    raw_machine = platform.machine() if machine is None else machine
    arch = _ARCH_NAMES.get(raw_machine.lower(), raw_machine.lower())
    package = _PACKAGE_FOR_KIND.get(kind if kind is not None else _install_kind())
    if package is None:
        return None
    for row in payload.get("assets") or []:
        if isinstance(row, dict) and (row.get("os"), row.get("arch"), row.get("package")) == (
            os_name, arch, package,
        ):
            return row
    if os_name == "windows" and package == "setup" and arch == "x64":
        url = payload.get("installerUrl") or payload.get("installer_url")
        if url:
            sha = payload.get("installerSha256") or payload.get("sha256")
            return {"os": "windows", "arch": "x64", "package": "setup", "url": url,
                    "sha256": sha, "sig_url": None}
    return None


def linux_update_status(
    *, payload: dict[str, Any] | None = None, kind: str | None = None
) -> dict[str, Any]:
    """The Linux counterpart of version_check.get_app_update_status(): same keys, plus
    ``install_kind``, ``update_action`` (restart | install | manual | none), ``asset``
    and ``last_failure`` (an AppImage update that was rolled back)."""
    from frontend import app_versions
    from frontend.version_check import (
        absolute_installer_url,
        download_page_url,
        extract_remote_version,
        feed_is_paused,
        fetch_remote_payload,
        update_base_url,
    )

    kind = kind or _install_kind()
    result: dict[str, Any] = {
        "local_version": __version__,
        "remote_version": None,
        "update_available": False,
        "installed": False,
        "channel": "portable" if kind == "source" else "installed",
        "install_kind": kind,
        "update_action": UPDATE_ACTIONS.get(kind, "none"),
        "asset": None,
        "installer_url": None,
        "installer_sha256": None,
        "release_notes": None,
        "download_url": download_page_url(),
        "feed_status": "none",
        "error": None,
        "last_failure": app_versions.appimage_swap_failure(),
    }
    if kind == "source":
        return result
    from frontend.ui_web.web_dev import is_frozen_dev_exe

    if is_frozen_dev_exe():
        result["channel"] = "dev"
        return result
    error = None
    if payload is None:
        payload, error = fetch_remote_payload(timeout=5.0, attempts=2)
    if error or not payload:
        result.update(feed_status="error", error=error or "Empty version-check response")
        return result
    result["release_notes"] = payload.get("releaseNotes") or payload.get("notes")
    if feed_is_paused(payload):
        result["feed_status"] = "paused"
        return result
    remote = extract_remote_version(payload)
    result["remote_version"] = remote
    if not remote:
        result["feed_status"] = "no_release"
        return result
    if not is_remote_newer(__version__, remote):
        result["feed_status"] = "up_to_date"
        return result
    asset = pick_asset(payload, kind=kind)
    if asset is not None:
        asset = dict(asset)
        base = update_base_url()
        asset["url"] = absolute_installer_url(asset.get("url"), base_url=base)
        asset["sig_url"] = absolute_installer_url(asset.get("sig_url"), base_url=base)
        result.update(installer_url=asset["url"], installer_sha256=asset.get("sha256"))
    result.update(feed_status="update_available", update_available=True, asset=asset)
    return result


def apply_linux_update(*, status: dict[str, Any] | None = None, run: Any = None) -> dict[str, Any]:
    """'Restart' on an AppImage (download, verify, swap, restart, roll back if it doesn't
    start); 'Install' on deb / rpm / Flatpak (the package manager does it)."""
    status = status or linux_update_status()
    if status.get("channel") == "dev":
        # Same words as the Windows path in apply_update().
        return _result(
            ok=False,
            error="Dev builds cannot self-update — only production installs update from the Store.",
            stage="check",
        )
    if not status["update_available"]:
        return _result(ok=False, error=status.get("error") or "No update available.", stage="check")
    kind = status["install_kind"]
    if kind in ("deb", "rpm"):
        return install_package_update(kind, status.get("asset") or {}, run=run)
    if kind == "flatpak":
        return hand_off_to_package_manager(kind, run=run)
    if kind == "aur":
        return _result(ok=False, error="Update with your AUR helper, e.g. yay -Syu uefn-ducky.",
                       stage="check")
    if kind != "appimage":
        return _result(ok=False, error="This copy can't update itself — get the new version from "
                       f"{status['download_url']}.", stage="check")
    asset = status.get("asset") or {}
    url, sha256 = asset.get("url"), asset.get("sha256")
    if not url:
        return _result(ok=False, error="The update feed has no AppImage for this computer.",
                       stage="check")
    if not sha256:
        return _result(ok=False, error="Update feed is missing the AppImage's sha256 — refusing to "
                       "install.", stage="check")
    if not installer_url_allowed(url):
        return _result(ok=False, error=f"Refusing non-HTTPS update URL: {url}", stage="check")
    app = Path(os.environ.get("APPIMAGE") or "")
    if not app.is_file():
        return _result(ok=False, error="Can't find the running AppImage ($APPIMAGE).", stage="check")
    _stop_all_agents()
    # ponytail: full download every time; add the .zsync delta when AppImages get big.
    new = app.with_name(app.name + ".new")
    error = _download(url, new)
    sig_url = asset.get("sig_url")
    sig_file = new.with_name(new.name + ".minisig")
    if not error and sig_url:
        error = _download(sig_url, sig_file)
    if error:
        _unlink_quiet(new)
        _unlink_quiet(sig_file)
        return _result(ok=False, error=error, stage="download")
    _set_progress(stage="verify", error=None)
    sig = None
    if sig_url:
        try:
            sig = sig_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            sig = ""
        _unlink_quiet(sig_file)
    error = install_appimage(app, new, sha256=sha256, signature=sig,
                             version=str(status["remote_version"]))
    if error:
        return _result(ok=False, error=error, stage="verify")
    _set_progress(stage="restarting", error=None)
    try:
        start_watchdog(app, version=str(status["remote_version"]))
    except OSError as exc:
        restore_previous(app)
        return _result(ok=False, error=f"Couldn't start the new version: {exc}", stage="launch")
    _exit_self_after_delay()
    return _result(ok=True, error=None, stage="restarting")


def verify_minisign(path: Path, signature: str, public_key: str) -> str | None:
    """Check a minisign signature (prehashed ``ED`` or legacy ``Ed``) and its trusted
    comment against ``public_key``. Returns an error string or ``None``."""
    import base64

    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    bad = "The update's signature doesn't match — refusing to install it."
    try:
        key = base64.b64decode([ln for ln in public_key.splitlines() if ln.strip()][-1])
        lines = [ln for ln in signature.splitlines() if ln.strip()]
        sig = base64.b64decode(lines[1])
        trusted = lines[2]
        global_sig = base64.b64decode(lines[3])
    except (IndexError, ValueError):
        return bad
    if (len(key) != 42 or len(sig) != 74 or len(global_sig) != 64
            or not trusted.startswith("trusted comment: ") or sig[2:10] != key[2:10]):
        return bad
    try:
        if sig[:2] == b"ED":
            digest = hashlib.blake2b(digest_size=64)
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    digest.update(chunk)
            message = digest.digest()
        elif sig[:2] == b"Ed":
            message = path.read_bytes()
        else:
            return bad
        public = Ed25519PublicKey.from_public_bytes(key[10:])
        public.verify(sig[10:], message)
        public.verify(global_sig, sig[10:] + trusted[len("trusted comment: "):].encode())
    except (InvalidSignature, OSError, ValueError):
        return bad
    return None


def install_appimage(
    app: Path, new: Path, *, sha256: str, signature: str | None, version: str
) -> str | None:
    """Verify ``new`` and swap it in for ``app``; the old file stays as ``<app>.previous``.
    On any error ``new`` is deleted and ``app`` is untouched."""
    from frontend import app_versions

    error = _verify_sha256(new, sha256)
    if not error and MINISIGN_PUBLIC_KEY:
        error = (verify_minisign(new, signature, MINISIGN_PUBLIC_KEY) if signature
                 else "The update has no signature — refusing to install it.")
    if not error:
        with new.open("rb") as f:
            if f.read(4) != b"\x7fELF":
                error = "The download isn't an AppImage."
    if error:
        _unlink_quiet(new)
        return error
    previous = app.with_name(app.name + ".previous")
    try:
        os.chmod(new, 0o755)
        # A hard link keeps $APPIMAGE in place the whole time; the rename is atomic.
        _unlink_quiet(previous)
        try:
            os.link(app, previous)
        except OSError:
            shutil.copy2(app, previous)
        os.replace(new, app)
    except OSError as exc:
        _unlink_quiet(new)
        return f"Couldn't replace the AppImage: {exc}"
    app_versions.begin_appimage_swap(version, from_version=__version__, app=app, previous=previous)
    return None


def restore_previous(app: Path) -> bool:
    previous = app.with_name(app.name + ".previous")
    try:
        os.replace(previous, app)
        return True
    except OSError:
        return False


def _clean_child_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _RUNTIME_ENV}
    # PyInstaller points LD_LIBRARY_PATH into its own bundle; give back the person's own.
    orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
    env.pop("LD_LIBRARY_PATH", None)
    if orig:
        env["LD_LIBRARY_PATH"] = orig
    return env


def start_watchdog(
    app: Path, *, version: str, timeout_s: int = HEALTHY_WITHIN_S, sh: str = "/bin/sh"
) -> subprocess.Popen[Any]:
    """Start the swapped-in AppImage under the rollback watchdog, detached from this one."""
    from frontend import app_versions

    _state, ok, failed = app_versions.appimage_swap_paths()
    previous = app.with_name(app.name + ".previous")
    return subprocess.Popen(
        [sh, "-c", _WATCHDOG_SH, "ducky-update-watch", str(app), str(previous), str(ok),
         str(failed), str(int(timeout_s)), __version__, version, str(os.getpid())],
        env=_clean_child_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )


def _run_package_command(cmd: list[str]) -> tuple[int, str]:
    try:
        # No stdin: nothing may wait on a terminal the person can't see. The person's own
        # library path, not the frozen bundle's (apt / dnf load the system's libraries).
        done = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              errors="replace", env=_clean_child_env(), timeout=_PACKAGE_TIMEOUT_S)
    except FileNotFoundError:
        return 127, ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return done.returncode, (done.stdout or "") + (done.stderr or "")


# What a deb / rpm copy without pkexec runs instead (the site's one-line installer).
INSTALL_SH = "curl -fsSL https://uefnducky.org/linux/install.sh | sh"


def package_update_command(kind: str, *, env: dict[str, str] | None = None) -> list[str]:
    """Flatpak's own update. deb / rpm install a downloaded file instead: package_install_command."""
    env = os.environ if env is None else env
    app_id = env.get("FLATPAK_ID") or ""
    # Inside the sandbox flatpak itself lives on the host.
    return ["flatpak-spawn", "--host", "flatpak", "update", "-y", "--noninteractive", app_id]


def package_install_command(path: Path, kind: str, *, which: Any = shutil.which) -> list[str]:
    """pkexec + the distro's own installer. polkit shows the desktop's password window.

    Never pkcon: it asks "Proceed with changes?" on the controlling terminal (not stdin),
    so started from a terminal it waited there forever, and without one it declined; and
    its -y made the transaction non-interactive, so polkit never asked ("Failed to obtain
    authentication"). Never write over the package's files ourselves. The file's SHA-256
    and minisign signature were checked before this runs (hence no repo signature check)."""
    if kind == "deb":
        return ["pkexec", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", str(path)]
    if which("dnf") or not which("zypper"):
        return ["pkexec", "dnf", "install", "-y", "--nogpgcheck", str(path)]
    return ["pkexec", "zypper", "--non-interactive", "install", "--allow-unsigned-rpm", str(path)]


def _run_manager(cmd: list[str], manager: str, run: Any) -> dict[str, Any]:
    _set_progress(stage="installing", error=None)
    code, output = (run or _run_package_command)(cmd)
    if code == 127 and not (output or "").strip():  # the program itself isn't installed
        fix = (f"run {INSTALL_SH} in a terminal" if manager == "pkexec"
               else "update UEFN Ducky from your software updater")
        return _result(ok=False, error=f"{manager} isn't available — {fix}.", stage="installing")
    # pkexec: 126 = the password window was closed, 127 = not authorized.
    if manager == "pkexec" and code in (126, 127):
        return _result(ok=False, error="The update needs your password and it wasn't given. "
                       f"Press Update again and enter it, or run {INSTALL_SH} in a terminal.",
                       stage="installing")
    if code != 0:
        tail = (output or "").strip().splitlines()[-1:] or [f"exit code {code}"]
        return _result(ok=False, error=f"The update didn't install: {tail[0]}", stage="installing")
    return _result(ok=True, error=None, stage="installed")


def hand_off_to_package_manager(
    kind: str, *, run: Any = None, env: dict[str, str] | None = None
) -> dict[str, Any]:
    """Flatpak through flatpak. The new version is used from the next start."""
    cmd = package_update_command(kind, env=env)
    if not cmd[-1]:
        return _result(ok=False, error="Can't tell this Flatpak's app id ($FLATPAK_ID).", stage="check")
    return _run_manager(cmd, "Flatpak", run)


def install_package_update(
    kind: str, asset: dict[str, Any], *, run: Any = None, download: Any = None
) -> dict[str, Any]:
    """deb / rpm: download the feed's package, check it like an AppImage update, and have
    PackageKit install that file. The new version is used from the next start.

    Never ``pkcon update``: the site's installer installs the downloaded file and adds no
    apt / dnf repo, so PackageKit had no newer version to find ("No packages were found")."""
    import tempfile

    url, sha256 = asset.get("url"), asset.get("sha256")
    if not url or not sha256:
        return _result(ok=False, error=f"The update feed has no .{kind} for this computer — "
                       f"run {INSTALL_SH} in a terminal.", stage="check")
    if not installer_url_allowed(url):
        return _result(ok=False, error=f"Refusing non-HTTPS update URL: {url}", stage="check")
    download = download or _download
    folder = Path(tempfile.mkdtemp(prefix="uefn-ducky-update-"))
    try:
        # packagekitd and apt's _apt user both read the file.
        os.chmod(folder, 0o755)
        pkg = folder / f"uefn-ducky.{kind}"
        sig_file = folder / (pkg.name + ".minisig")
        sig_url = asset.get("sig_url")
        error = download(url, pkg)
        if not error and sig_url:
            error = download(sig_url, sig_file)
        if error:
            return _result(ok=False, error=error, stage="download")
        _set_progress(stage="verify", error=None)
        error = _verify_sha256(pkg, sha256)
        if not error and MINISIGN_PUBLIC_KEY:
            sig = sig_file.read_text(encoding="utf-8", errors="replace") if sig_file.is_file() else ""
            error = (verify_minisign(pkg, sig, MINISIGN_PUBLIC_KEY) if sig
                     else "The update has no signature — refusing to install it.")
        if error:
            return _result(ok=False, error=error, stage="verify")
        os.chmod(pkg, 0o644)
        return _run_manager(package_install_command(pkg, kind), "pkexec", run)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
