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

# The native launcher. Its name never starts with ``UEFN-Ducky`` so the sweeps in older
# versions (``UEFN-Ducky*``) never match it.
LAUNCHER_EXE = "Ducky-Launcher.exe"
BACKEND_FLAG = "--backend"


def is_launcher_name(name: str) -> bool:
    """``Ducky-Launcher.exe`` (or its ``ProcessName`` without ``.exe``)."""
    stem = Path(str(name or "")).name.lower()
    if stem.endswith(".exe"):
        stem = stem[:-4]
    return stem == LAUNCHER_EXE[:-4].lower()


def is_backend_cmdline(cmdline: list[str] | tuple[str, ...] | None) -> bool:
    """``UEFN-Ducky.exe --backend ...``: the Python side of a launcher start."""
    args = [str(a).strip().lower() for a in (cmdline or [])[1:]]
    return BACKEND_FLAG in args


def _process_identity(pid: int) -> tuple[str, list[str]] | None:
    """``(name, cmdline)`` of ``pid``, or None when it can't be read."""
    try:
        import psutil

        proc = psutil.Process(int(pid))
        return str(proc.name() or ""), [str(a) for a in (proc.cmdline() or [])]
    except Exception:
        return None


def is_launcher_side(pid: int) -> bool:
    """``pid`` is ``Ducky-Launcher.exe`` or a ``UEFN-Ducky.exe --backend`` it started.

    Neither is ever reclaimed: the launcher owns its backend's life (crash loop, exit
    codes) and a new start hands off to it through ``panel.pid`` like to any window.
    """
    ident = _process_identity(pid)
    if ident is None:
        return False
    name, cmdline = ident
    return is_launcher_name(name) or is_backend_cmdline(cmdline)


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
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            creationflags=flags,
            startupinfo=si,
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

    Skips the Setup host (``UEFN-Ducky-Setup*``) so an in-place upgrade cannot
    reap the installer. The inner engine is ``Setup-engine.exe`` and never matches.
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
    from frontend.open_files import panel_process_alive

    # Nothing to kill when it already exited (taskkill on it still cost ~5 s).
    if old_pid <= 0 or old_pid == os.getpid() or not panel_process_alive(old_pid):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    # A launcher start (or its backend) is alive and owns panel.pid: hand off, never kill.
    if is_launcher_side(old_pid):
        return False
    reclaimed = _kill_pid_tree(old_pid)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return reclaimed


def _panel_answers(port: int, timeout_s: float = 3.0) -> bool:
    """Whatever listens on the panel port answers HTTP (any status): it is alive, maybe busy."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=timeout_s):
            return True
    except urllib.error.HTTPError:
        return True
    except (OSError, ValueError):
        return False


def reclaim_panel_port(port: int) -> bool:
    """Close a stuck Ducky still listening on the panel port (not the one in ``panel.pid``).

    Runs after the hand-off to a living panel failed, so whatever still holds the port is
    not answering; left alone it would make this launch fail to start its server. Only
    ``UEFN-Ducky*`` processes are touched (never Setup, never a dev server).
    """
    if sys.platform != "win32":
        return False
    try:
        import psutil

        listeners = [
            c.pid
            for c in psutil.net_connections(kind="tcp")
            if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == int(port) and c.pid
        ]
    except Exception:
        return False
    # Only a copy that doesn't answer is stuck. Oct 21 2026: a Ducky started with another
    # AppData (no panel.pid of its own, so no hand-off) killed the person's live panel,
    # busy for a few seconds, mid-run; it ended with no crash trace.
    if listeners and _panel_answers(int(port)):
        return False
    closed = False
    for pid in listeners:
        if pid == os.getpid():
            continue
        try:
            name = psutil.Process(pid).name().lower()
        except Exception:
            continue
        if is_launcher_side(pid):
            continue
        if name.startswith("uefn-ducky") and "setup" not in name:
            closed = _kill_pid_tree(pid) or closed
    return closed


def claim_panel_process() -> None:
    """Record this panel PID so the next launch can hand off to it (or reclaim a zombie).

    A plain integer in both modes (window or ``--backend``): every older version reads
    ``int(panel.pid)``, so an old window hands off to a launcher's backend and back.
    """
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
