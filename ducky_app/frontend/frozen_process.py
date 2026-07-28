"""Hard shutdown and stale-panel cleanup for the frozen Windows EXE."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from frontend.app_paths import (
    exe_name_matches,
    kill_process_ps_filter,
    process_name_matches,
    resolve_app_data_dir,
)
from frontend.bundle_root import is_packaged_runtime

# PyInstaller base name (``build/unified.spec``); release builds may be ``UEFN-Ducky-<version>.exe``.


def is_uefn_ducky_executable(path: Path | str) -> bool:
    return exe_name_matches(Path(path).name)


def is_uefn_ducky_process_name(process_name: str) -> bool:
    """Windows ``ProcessName`` has no ``.exe`` suffix."""
    return process_name_matches(process_name)


def panel_pid_path(*, for_write: bool = False) -> Path:
    return resolve_app_data_dir(for_write=for_write) / "panel.pid"


def _run_hidden(args: list[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess[str] | None:
    if sys.platform != "win32":
        return None
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            creationflags=flags,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _kill_pid_tree(pid: int) -> bool:
    r = _run_hidden(["taskkill", "/F", "/PID", str(int(pid)), "/T"])
    return r is not None and r.returncode == 0


def kill_uefn_ducky_processes(*, include_self: bool = True, exclude_pid: int | None = None) -> bool:
    """
    Terminate ``UEFN-Ducky*.exe`` processes (panel + IDE ``bridge`` workers).

    Uses PowerShell so versioned names like ``UEFN-Ducky-1.0.14.exe`` are matched.
    """
    if sys.platform != "win32":
        return False

    my_pid = os.getpid()
    if exclude_pid is None and not include_self:
        exclude_pid = my_pid

    if exclude_pid is not None:
        ps = (
            f"Get-Process | Where-Object {{ ({kill_process_ps_filter()}) "
            f"-and $_.Id -ne {int(exclude_pid)} }} | Stop-Process -Force"
        )
    else:
        ps = f"Get-Process | Where-Object {{ {kill_process_ps_filter()} }} | Stop-Process -Force"

    # Avoid stranding panel.pid across self force-kills (update / hard exit) so the
    # next launch does not wait on a dead PID handoff. Do not clear when only
    # killing sibling bridges — this panel is still alive.
    if include_self:
        try:
            panel_pid_path().unlink(missing_ok=True)
        except OSError:
            pass

    r = _run_hidden(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps])
    if r is not None and r.returncode == 0:
        return True

    if include_self and is_packaged_runtime() and is_uefn_ducky_executable(sys.executable):
        return _kill_pid_tree(my_pid)
    return False


def try_kill_all_uefn_ducky_exe() -> bool:
    """
    When running as a packaged ``UEFN-Ducky*.exe``, terminate every instance
    (this panel and any IDE ``bridge`` workers).
    """
    if sys.platform != "win32" or not is_packaged_runtime():
        return False
    if not is_uefn_ducky_executable(sys.executable):
        return False
    return kill_uefn_ducky_processes(include_self=True)


def reclaim_stale_panel_process() -> bool:
    """
    On panel startup: kill a previous panel instance left in ``panel.pid`` (zombie after bad exit).

    Does not touch IDE ``bridge`` workers (they do not write ``panel.pid``).
    Always clears ``panel.pid`` so a force-killed update cannot strand the next launch.
    """
    if sys.platform != "win32":
        return False
    path = panel_pid_path()
    if not path.is_file():
        return False
    try:
        old_pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    if old_pid <= 0 or old_pid == os.getpid():
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    reclaimed = _kill_pid_tree(old_pid)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return reclaimed


def claim_panel_process() -> None:
    """Record this panel PID so the next launch can reclaim a zombie."""
    if sys.platform != "win32":
        return
    try:
        panel_pid_path(for_write=True).write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass


def release_panel_process() -> None:
    """Clear ``panel.pid`` when the panel exits cleanly."""
    path = panel_pid_path()
    try:
        if path.is_file() and int(path.read_text(encoding="utf-8").strip()) == os.getpid():
            path.unlink()
    except (OSError, ValueError):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
