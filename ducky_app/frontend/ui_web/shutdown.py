"""Reliable process shutdown for the pywebview panel (Tk after() is not pumped)."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Callable
from typing import Any

from frontend.app_paths import kill_process_ps_filter
from frontend.frozen_process import kill_uefn_ducky_processes, release_panel_process

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_exit_lock = threading.Lock()
_exit_started = False

# Cross-thread work for the Tk pump. pywebview bridge threads must not call
# ``tk_root.after`` — that raises "main thread is not in main loop".
_tk_call_queue: queue.Queue[Callable[[], None]] = queue.Queue()


def schedule_tk_call(fn: Callable[[], None]) -> None:
    """Run ``fn`` on the Tk pump thread (safe from JS-bridge workers)."""
    _tk_call_queue.put(fn)


def _stop_all_agents() -> None:
    try:
        from frontend.ui_web.agent_modes import join_running_agents

        join_running_agents(0.5)
    except Exception:
        pass


def _stop_all_terminals() -> None:
    try:
        from frontend.ui_web.terminal import get_terminal_manager

        get_terminal_manager().shutdown_all()
    except Exception:
        pass


def _stop_mcp_plugins() -> None:
    try:
        import asyncio

        from backend.mcp_plugins.client_pool import get_plugin_pool

        asyncio.run(get_plugin_pool().shutdown_all())
    except Exception:
        pass


def _terminate_process() -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.TerminateProcess(
                ctypes.windll.kernel32.GetCurrentProcess(),
                0,
            )
        except Exception:
            pass
    os._exit(0)


def _spawn_kill_uefn_processes() -> None:
    """Fire-and-forget reaper for sibling UEFN-Ducky processes (panel + IDE bridge workers).

    Spawned **detached** so it outlives this panel's own termination. The old code spawned
    ``taskkill /F /PID <self> /T`` alongside the PowerShell reaper as siblings — the ``/T`` killed
    self's whole child tree, which includes the reaper, so the bridge workers often survived
    "Exit all". Now one detached PowerShell waits for the panel to die, then force-kills every
    remaining matching process. (IDE-managed bridges may still be respawned by the IDE itself —
    that is outside our control; close/disable the IDE's UEFN MCP to stop those for good.)
    """
    if sys.platform != "win32":
        return
    # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: no console, and not reaped with the panel.
    flags = _CREATE_NO_WINDOW | 0x00000008 | 0x00000200
    ps = (
        "Start-Sleep -Milliseconds 300; "
        f"Get-Process | Where-Object {{ {kill_process_ps_filter()} }} | Stop-Process -Force"
    )
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            creationflags=flags,
            close_fds=True,
        )
    except Exception:
        pass


def _stop_tray(api: Any) -> None:
    tray = getattr(api, "_tray", None)
    if tray is None:
        return
    try:
        t = threading.Thread(target=tray.stop, name="tray-stop", daemon=True)
        t.start()
        t.join(0.2)
    except Exception:
        pass
    try:
        api._tray = None  # type: ignore[attr-defined]
    except Exception:
        pass


def _kill_sibling_processes() -> None:
    """Terminate other UEFN-Ducky bridge workers before this process exits."""
    if sys.platform != "win32":
        return
    try:
        kill_uefn_ducky_processes(include_self=False, exclude_pid=os.getpid())
        time.sleep(0.12)
    except Exception:
        pass


def hard_exit(*, api: Any = None) -> None:
    """Kill this process immediately. Must not return."""
    global _exit_started
    with _exit_lock:
        if _exit_started:
            return
        _exit_started = True

    _stop_all_agents()
    _stop_all_terminals()
    _stop_mcp_plugins()
    release_panel_process()
    if api is not None:
        _stop_tray(api)
    _kill_sibling_processes()
    _spawn_kill_uefn_processes()
    _terminate_process()


def _format_fatal_message(exc: BaseException | str) -> str:
    if isinstance(exc, str):
        return exc
    msg = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    if not msg:
        msg = repr(exc)
    text = str(exc)
    if "Panel build incomplete" in text or "React build not found" in text:
        return (
            f"{msg}\n\n"
            "Rebuild the panel UI (cd ducky_app/frontend/ui_web/web && npm run build), "
            "then rebuild or restart the app."
        )
    return msg


def fatal_error_and_exit(
    exc: BaseException | str,
    *,
    api: Any = None,
    tk_root: Any = None,
) -> None:
    """Show a blocking error dialog when possible, then terminate the whole process."""
    message = _format_fatal_message(exc) if not isinstance(exc, str) else exc
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk_root
        owns_root = False
        if root is None:
            root = tk.Tk()
            root.withdraw()
            owns_root = True
        try:
            messagebox.showerror("UEFN Ducky", message, parent=root)
        finally:
            if owns_root:
                try:
                    root.destroy()
                except Exception:
                    pass
    except Exception:
        pass
    hard_exit(api=api)


def _start_hard_exit(*, api: Any = None) -> None:
    """Run hard_exit off the pywebview API thread so Exit all does not freeze the UI."""
    threading.Thread(
        target=lambda: hard_exit(api=api),
        name="app-exit-hard",
        daemon=True,
    ).start()


def arm_force_exit(delay: float = 0.15, *, api: Any = None) -> None:
    """Backup kill if the primary exit path stalls. Safe to call repeatedly."""
    threading.Timer(max(0.04, float(delay)), lambda: hard_exit(api=api)).start()


def _best_effort_cleanup(*, api: Any, tk_root: Any, window_holder: dict[str, object]) -> None:
    _stop_tray(api)
    w = window_holder.get("window")
    if w is not None:
        try:
            w.destroy()
        except Exception:
            pass
    if tk_root is not None:
        try:
            tk_root.quit()
        except Exception:
            pass
        try:
            tk_root.destroy()
        except Exception:
            pass


def abort_and_quit(
    *,
    api: Any,
    tk_root: Any,
    window_holder: dict[str, object],
) -> None:
    """Exit even when an agent is running or HTTP/tool calls are in flight."""
    _stop_all_agents()
    arm_force_exit(0.08, api=api)
    arm_force_exit(0.2, api=api)
    arm_force_exit(0.45, api=api)
    threading.Thread(
        target=_best_effort_cleanup,
        kwargs={"api": api, "tk_root": tk_root, "window_holder": window_holder},
        name="app-exit-cleanup",
        daemon=True,
    ).start()
    _start_hard_exit(api=api)


def request_app_exit(
    *,
    api: Any,
    tk_root: Any,
    window_holder: dict[str, object],
) -> None:
    abort_and_quit(api=api, tk_root=tk_root, window_holder=window_holder)


def start_tk_pump(tk_root: Any) -> None:
    """Pump Tk events so tray menu + filedialog callbacks run under pywebview."""

    def _pump() -> None:
        while True:
            # Marshal from bridge threads via after() on THIS thread — never call
            # tk_root.after from a pywebview JS-API worker.
            try:
                while True:
                    fn = _tk_call_queue.get_nowait()
                    try:
                        tk_root.after(0, fn)
                    except Exception:
                        pass
            except queue.Empty:
                pass
            try:
                tk_root.update()
            except Exception:
                break
            time.sleep(0.05)

    threading.Thread(target=_pump, name="tk-pump", daemon=True).start()
