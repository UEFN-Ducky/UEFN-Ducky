"""Hidden-window subprocess executor for CLI coding agents.

Replaces the PTY/PowerShell scrape path for non-interactive runs: env is passed
as a real environment dict (no ``$env:`` one-liner quoting), stdout is consumed
line-by-line for JSON event streams, and the process handle is registered per
conversation so cancel actually kills the CLI.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable

_procs: dict[str, subprocess.Popen] = {}
_procs_lock = threading.Lock()

_STDERR_TAIL_CHARS = 8000
_STDOUT_TAIL_CHARS = 4000


def _shared_daemon_pid() -> int:
    try:
        from backend.bridge.shared_mcp import read_state

        return int((read_state() or {}).get("pid") or 0)
    except Exception:
        return 0


def _kill_windows_tree(pid: int) -> None:
    """Terminate ``pid`` and its descendants.

    ``taskkill /F /T`` deadlocks here: the CLI's stdout is a pipe, taskkill waits
    for the parent to exit, and the parent cannot exit while a child still holds
    the inherited pipe. A 5s timeout then falls through to ``proc.kill()``, which
    leaves MCP children alive.
    """
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel.Process32NextW.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]

    snap = kernel.CreateToolhelp32Snapshot(0x00000002, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        return
    kids: dict[int, list[int]] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            kids.setdefault(int(entry.th32ParentProcessID), []).append(int(entry.th32ProcessID))
            ok = kernel.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snap)

    order: list[int] = []
    stack = [int(pid)]
    # The shared MCP daemon serves every agent. One this agent's adapter started sits
    # in its tree; killing it cut all the other agents off mid-run.
    seen: set[int] = {_shared_daemon_pid()} - {0}
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(kids.get(cur, []))
        order.append(cur)
    for target in reversed(order):
        handle = kernel.OpenProcess(0x0001, False, target)
        if not handle:
            continue
        kernel.TerminateProcess(handle, 1)
        kernel.CloseHandle(handle)


def _terminate_process_tree(proc: subprocess.Popen) -> None:
    """Cancel this owned CLI tree before killing its parent on Windows.

    A parent-only kill can leave MCP children alive holding stdout/stderr open.
    Do not use this on normal completion: agents can deliberately start services
    that must outlive a turn. Never target an executable name or another client.
    """
    if proc.poll() is not None:
        return
    if os.name == "nt":
        _kill_windows_tree(int(proc.pid))
    if proc.poll() is None:
        proc.kill()


def register_process(conv_id: str, proc: subprocess.Popen) -> None:
    with _procs_lock:
        prev = _procs.get(conv_id)
        _procs[conv_id] = proc
    if prev is not None and prev is not proc:
        try:
            _terminate_process_tree(prev)
        except OSError:
            pass


def unregister_process(conv_id: str, proc: subprocess.Popen) -> None:
    with _procs_lock:
        if _procs.get(conv_id) is proc:
            _procs.pop(conv_id, None)


def terminate_conv_process(conv_id: str) -> bool:
    """Kill the running coding-agent process for a chat (cancel path)."""
    with _procs_lock:
        proc = _procs.get(conv_id)
    if proc is None or proc.poll() is not None:
        return False
    try:
        _terminate_process_tree(proc)
    except OSError:
        return False
    return True


@dataclass
class ProcResult:
    returncode: int
    timed_out: bool = False
    cancelled: bool = False
    stderr_tail: str = ""
    stdout_lines: int = 0
    raw_tail: str = ""
    """Last unparsed stdout text, for error surfaces when no JSON arrived."""


def run_streaming_process(
    *,
    argv: list[str],
    cwd: str,
    env_extra: dict[str, str],
    conv_id: str,
    on_line: Callable[[str], None],
    timeout_s: float,
    cancel: threading.Event | None = None,
    stdin_data: str | None = None,
) -> ProcResult:
    """Run argv, feeding each stdout line to ``on_line``. Kills on timeout/cancel.

    Pass long user prompts via ``stdin_data`` — never as argv — or Windows
    CreateProcess raises WinError 206 on large pastes.
    """
    env = dict(os.environ)
    for key, value in (env_extra or {}).items():
        if key:
            env[str(key)] = str(value)

    workdir = (cwd or "").strip() or os.getcwd()
    if not os.path.isdir(workdir):
        workdir = os.getcwd()

    use_stdin = stdin_data is not None
    kwargs: dict = {
        "cwd": workdir,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.PIPE if use_stdin else subprocess.DEVNULL,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        proc = subprocess.Popen(argv, **kwargs)
    except OSError as exc:
        # WinError 206 / errno-equivalent: command line or env still too long.
        win = getattr(exc, "winerror", None)
        if win == 206 or "too long" in str(exc).lower():
            raise OSError(
                206,
                "Prompt/command too long for Windows to launch the coding agent. "
                "Ducky should pipe the prompt via stdin/file — restart UEFN-Ducky "
                "and update the Anthropic (Claude Code) Store plugin if this persists.",
            ) from exc
        raise
    register_process(conv_id, proc)
    result = ProcResult(returncode=-1)

    if use_stdin and proc.stdin is not None:
        try:
            proc.stdin.write(stdin_data or "")
            proc.stdin.close()
        except (OSError, ValueError, BrokenPipeError):
            pass

    def _drain_stderr() -> None:
        try:
            for line in proc.stderr or []:
                result.stderr_tail = (result.stderr_tail + line[-_STDERR_TAIL_CHARS:])[-_STDERR_TAIL_CHARS:]
        except (OSError, ValueError):
            pass

    def _drain_stdout() -> None:
        try:
            for line in proc.stdout or []:
                stripped = line.rstrip("\r\n")
                if not stripped:
                    continue
                result.stdout_lines += 1
                result.raw_tail = (result.raw_tail + "\n" + stripped[-_STDOUT_TAIL_CHARS:])[-_STDOUT_TAIL_CHARS:].lstrip("\n")
                try:
                    on_line(stripped)
                except Exception:
                    # A presenter bug must not kill the read loop mid-turn.
                    pass
        except (OSError, ValueError):
            pass

    t_err = threading.Thread(target=_drain_stderr, daemon=True, name=f"ca-stderr-{conv_id[:8]}")
    t_out = threading.Thread(target=_drain_stdout, daemon=True, name=f"ca-stdout-{conv_id[:8]}")
    t_err.start()
    t_out.start()

    # timeout_s <= 0 means no wall-clock limit — keep going until the CLI exits
    # or the user cancels. Long UEFN builds (city blockouts, etc.) routinely need
    # more than 15 minutes; killing them mid-turn is worse than waiting.
    limit = float(timeout_s)
    deadline = (time.time() + limit) if limit > 0 else None
    try:
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                result.cancelled = True
                _terminate_process_tree(proc)
                break
            if deadline is not None and time.time() > deadline:
                result.timed_out = True
                _terminate_process_tree(proc)
                break
            time.sleep(0.1)
        proc.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass
    finally:
        t_out.join(timeout=5)
        t_err.join(timeout=2)
        unregister_process(conv_id, proc)

    result.returncode = proc.returncode if proc.returncode is not None else -1
    return result
