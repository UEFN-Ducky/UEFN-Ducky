"""Silent in-app update via the Inno Setup installer. Installed channel only."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
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


def _result(*, ok: bool, error: str | None, stage: str) -> dict[str, Any]:
    _set_progress(stage=stage, error=error)
    return {"ok": ok, "error": error, "stage": stage}


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


def _installer_process_running(dest: Path) -> bool:
    """True if a process with ``dest``'s image name is alive (elevated child too)."""
    if sys.platform != "win32":
        return False
    name = dest.name
    if not name:
        return False
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        completed = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
            capture_output=True,
            text=True,
            creationflags=flags,
            timeout=10.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return name.lower() in (completed.stdout or "").lower()


def _setup_still_running_after_wait(dest: Path) -> bool:
    """True when Setup is still alive after the Popen handle exited (UAC elevation)."""
    deadline = time.monotonic() + _ELEVATION_HANDOFF_S
    while time.monotonic() < deadline:
        if _installer_process_running(dest):
            return True
        time.sleep(0.15)
    return _installer_process_running(dest)


def _download(url: str, dest: Path, *, timeout: float = 120.0) -> str | None:
    """Download ``url`` to ``dest``. Returns an error string or ``None``."""
    global _active_download_resp
    req = urllib.request.Request(url, headers={"User-Agent": f"UEFN-Ducky/{__version__}"})
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(req, timeout=timeout) as resp, dest.open("wb") as out:
            with _active_download_lock:
                _active_download_resp = resp
            try:
                total = 0
                try:
                    total = int(resp.headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    total = 0
                downloaded = 0
                _set_progress(stage="download", downloaded_bytes=0, total_bytes=total, error=None)
                while True:
                    if _cancelled():
                        return _CANCELLED
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    out.write(chunk)
                    downloaded += len(chunk)
                    _set_progress(downloaded_bytes=downloaded, total_bytes=total)
            finally:
                with _active_download_lock:
                    _active_download_resp = None
    except (OSError, urllib.error.URLError, ValueError) as exc:
        if _cancelled():
            return _CANCELLED
        return str(exc)
    return _CANCELLED if _cancelled() else None


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


def _shutdown_after_delay() -> None:
    """Reap the panel and IDE bridge workers so the installer can replace files."""

    def _shutdown() -> None:
        try:
            from frontend.frozen_process import kill_uefn_ducky_processes, release_panel_process

            # Clear panel.pid before force-kill so the relaunched EXE does not wait on
            # a dead/dying PID handoff (connect timeouts look like a 20–30s hang).
            release_panel_process()
            kill_uefn_ducky_processes(include_self=True)
        except Exception:
            pass
        # Root cause of stuck "Updating…" lock: PowerShell/taskkill can fail and
        # leave the panel alive with Cancel disabled. Always die so Setup can
        # replace the EXE and the overlay cannot strand the user.
        os._exit(0)

    threading.Timer(_SHUTDOWN_DELAY_S, _shutdown).start()


def _exit_self_after_delay() -> None:
    """Leave after a successful Setup wait without killing a relaunched panel."""

    def _exit() -> None:
        try:
            from frontend.frozen_process import release_panel_process

            release_panel_process()
        except Exception:
            pass
        os._exit(0)

    threading.Timer(_SHUTDOWN_DELAY_S, _exit).start()


def _stop_all_agents() -> None:
    """Cancel every running agent so the installer can replace the EXE cleanly."""
    _set_progress(stage="stopping_agents", error=None)
    try:
        from frontend.ui_web.agent_modes import cancel_agent

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
    if not url.lower().startswith("https://"):
        return _result(ok=False, error=f"Refusing non-HTTPS installer URL: {url}", stage="check")

    _stop_all_agents()
    if _cancelled():
        return _result(ok=False, error=_CANCELLED, stage="stopping_agents")

    dest = _installer_download_path(str(status["remote_version"]))
    sha256 = status["installer_sha256"]

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

        if sha256:
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

    _set_progress(stage="launch", error=None)
    # Drop IDE bridge workers before Setup starts so Restart Manager does not
    # wait (or prompt) on locked UEFN-Ducky*.exe handles. Do NOT exit the panel
    # yet — wait for Setup so a declined UAC keeps the app + cached installer.
    try:
        from frontend.frozen_process import kill_uefn_ducky_processes

        kill_uefn_ducky_processes(include_self=False)
    except Exception:
        pass
    try:
        proc = subprocess.Popen(
            [str(dest), *_silent_install_args(status["install_scope"])],
            close_fds=True,
        )
    except OSError as exc:
        return _result(ok=False, error=str(exc), stage="launch")

    _set_progress(stage="installing", error=None)
    try:
        code = int(proc.wait())
    except OSError as exc:
        return _result(ok=False, error=str(exc), stage="installing")

    # /ALLUSERS: unelevated stub often exits 0 right after UAC Yes while the
    # elevated Setup-{ver}.exe child is still installing. Do not delete the
    # cache or assume success until that child is gone.
    if _setup_still_running_after_wait(dest):
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
    """Run the registered quiet uninstaller and exit the app."""
    info = get_install_info()
    if not info["installed"]:
        return _result(ok=False, error="This copy is portable — nothing to uninstall.", stage="check")
    command = info["quiet_uninstall_command"] or info["uninstall_command"]
    if not command:
        return _result(ok=False, error="No uninstall command registered.", stage="check")

    try:
        # UninstallString is a full command line (quoted exe + args); pass it
        # through as-is so CreateProcess parses it the same way Windows does.
        subprocess.Popen(command, close_fds=True)
    except OSError as exc:
        return _result(ok=False, error=str(exc), stage="launch")

    _shutdown_after_delay()
    return _result(ok=True, error=None, stage="uninstalling")
