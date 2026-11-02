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
_writers: dict[str, "StdinWriter"] = {}
# conv id -> the adapter's polite stop (request_stop). Stop asks it first, kills after STOP_GRACE_S.
_stop_requesters: dict[str, Callable[[], object]] = {}

STOP_GRACE_S = 15.0

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
    exe: dict[int, str] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            kids.setdefault(int(entry.th32ParentProcessID), []).append(int(entry.th32ProcessID))
            exe[int(entry.th32ProcessID)] = str(entry.szExeFile or "")
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
    # A git killed mid-commit leaves .git/index.lock; note its repo now, clean up after.
    from backend.agent.coding_agents import git_lock_cleanup

    try:
        repos = git_lock_cleanup.repos_of([p for p in order if exe.get(p, "").lower() == "git.exe"])
    except Exception:
        repos = set()
    for target in reversed(order):
        handle = kernel.OpenProcess(0x0001, False, target)
        if not handle:
            continue
        kernel.TerminateProcess(handle, 1)
        kernel.CloseHandle(handle)
    git_lock_cleanup.clean_in_background(repos)


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


def get_process(conv_id: str) -> subprocess.Popen | None:
    """The chat's running coding-agent process, e.g. for an adapter's graceful Stop
    (``get_process(cid).send_signal(signal.CTRL_BREAK_EVENT)`` with ``new_process_group``)."""
    with _procs_lock:
        proc = _procs.get(conv_id)
    return proc if proc is not None and proc.poll() is None else None


def unregister_process(conv_id: str, proc: subprocess.Popen) -> None:
    with _procs_lock:
        if _procs.get(conv_id) is proc:
            _procs.pop(conv_id, None)


def set_stop_requester(conv_id: str, request_stop: Callable[[], object] | None) -> None:
    """Register (or with None clear) the adapter's ``request_stop`` for this chat's turn."""
    with _procs_lock:
        if request_stop is None:
            _stop_requesters.pop(conv_id, None)
        else:
            _stop_requesters[conv_id] = request_stop


def _stop(conv_id: str, proc: subprocess.Popen) -> None:
    """Cancel: ask the adapter to stop, give the turn ``STOP_GRACE_S`` to end, then tree-kill.

    A tree kill at once could cut a git commit or a file write in half. Adapters without
    ``request_stop`` (or whose request fails / returns False) are killed at once, as before.
    A second Stop (``terminate_conv_process``) kills during the wait.
    """
    with _procs_lock:
        ask = _stop_requesters.get(conv_id)
    asked = False
    if ask is not None:
        try:
            asked = ask() is not False
        except Exception:
            asked = False
    if asked:
        deadline = time.monotonic() + STOP_GRACE_S
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
    _terminate_process_tree(proc)


def terminate_conv_process(conv_id: str) -> bool:
    """Kill the running coding-agent process for a chat now (a second Stop)."""
    with _procs_lock:
        proc = _procs.get(conv_id)
    if proc is None or proc.poll() is not None:
        return False
    try:
        _terminate_process_tree(proc)
    except OSError:
        return False
    return True


class StdinWriter:
    """Line writer for a child's stdin that stays open for the whole turn.

    Writes after ``close()`` or after the child exited return False instead of raising.
    Nothing here waits on a write the child isn't reading: another write gives up after
    ``LOCK_WAIT_S`` (Stop then kills), and ``close()`` leaves the stream to that write.
    """

    LOCK_WAIT_S = 2.0

    def __init__(self, stream) -> None:
        self._stream = stream
        self._lock = threading.Lock()
        self._stream_closed = False
        self.closed = stream is None

    def write_line(self, text: str) -> bool:
        return self.write(str(text).rstrip("\r\n") + "\n")

    def write(self, text: str) -> bool:
        if not self._lock.acquire(timeout=self.LOCK_WAIT_S):
            return False
        return self._write_locked(text)

    def write_soon(self, text: str, *, name: str = "") -> None:
        """``text`` on its own thread, ahead of every later write (the lock is taken here)."""
        self._lock.acquire()
        threading.Thread(target=self._write_locked, args=(text,), daemon=True, name=name or None).start()

    def _write_locked(self, text: str) -> bool:
        """Write with the lock held; releases it."""
        try:
            if self.closed:
                return False
            try:
                self._stream.write(text)
                self._stream.flush()
                return True
            except (OSError, ValueError):
                self.closed = True
                return False
        finally:
            self._lock.release()
            if self.closed:  # close() came while this write held the lock: finish it now
                self.close()

    def close(self) -> None:
        # Never waits: a write holding the lock closes the stream itself once it returns.
        self.closed = True
        if self._lock.acquire(blocking=False):
            try:
                self._close_stream()
            finally:
                self._lock.release()

    def _close_stream(self) -> None:
        if self._stream is None or self._stream_closed:
            return
        self._stream_closed = True
        try:
            self._stream.close()
        except (OSError, ValueError):
            pass


def get_stdin_writer(conv_id: str) -> StdinWriter | None:
    """The open stdin writer of a chat's running ``keep_stdin_open`` process, if any."""
    with _procs_lock:
        return _writers.get(conv_id)


@dataclass
class ProcResult:
    returncode: int
    timed_out: bool = False
    cancelled: bool = False
    stderr_tail: str = ""
    stdout_lines: int = 0
    raw_tail: str = ""
    """Last unparsed stdout text, for error surfaces when no JSON arrived."""
    stdin_writer: StdinWriter | None = None
    """Set with ``keep_stdin_open``; always closed by the time the result returns."""


def cli_env(env_extra: dict[str, str] | None) -> dict[str, str]:
    """A CLI's environment: ours plus ``env_extra``, without our process role, so the
    CLI's own tool server never starts as an agent host."""
    from backend.process_role import ENV as ROLE_ENV

    env = dict(os.environ)
    env.pop(ROLE_ENV, None)
    for key, value in (env_extra or {}).items():
        if key:
            env[str(key)] = str(value)
    return env


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
    keep_stdin_open: bool = False,
    on_stdin_writer: Callable[[StdinWriter], None] | None = None,
    new_process_group: bool = False,
) -> ProcResult:
    """Run argv, feeding each stdout line to ``on_line``. Kills on timeout/cancel.

    Pass long user prompts via ``stdin_data`` — never as argv — or Windows
    CreateProcess raises WinError 206 on large pastes.

    ``keep_stdin_open``: write ``stdin_data`` but leave stdin open; the writer is
    handed to ``on_stdin_writer`` and served by ``get_stdin_writer(conv_id)`` while
    the process runs, and closed on exit, timeout and cancel.

    ``new_process_group`` (Windows): start the child in its own process group, so
    ``CTRL_BREAK_EVENT`` can reach it without hitting Ducky itself.
    """
    env = cli_env(env_extra)

    workdir = (cwd or "").strip() or os.getcwd()
    if not os.path.isdir(workdir):
        workdir = os.getcwd()

    use_stdin = stdin_data is not None or keep_stdin_open
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
        if new_process_group:
            kwargs["creationflags"] |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

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

    # Readers first, then the prompt on its own thread. Written here before them, a prompt
    # bigger than the pipe buffer deadlocked whenever the CLI wrote before reading (Claude
    # Code with stream-json input, Oct 25 2026): the turn sat on Thinking, deaf to Stop.
    if keep_stdin_open:
        writer = StdinWriter(proc.stdin)
        result.stdin_writer = writer
        with _procs_lock:
            _writers[conv_id] = writer
        if stdin_data:
            writer.write_soon(stdin_data, name=f"ca-stdin-{conv_id[:8]}")
        if on_stdin_writer is not None:
            try:
                on_stdin_writer(writer)
            except Exception:
                pass
    elif use_stdin and proc.stdin is not None:
        def _feed_stdin() -> None:
            try:
                proc.stdin.write(stdin_data or "")
                proc.stdin.close()
            except (OSError, ValueError, BrokenPipeError):
                pass

        threading.Thread(target=_feed_stdin, daemon=True, name=f"ca-stdin-{conv_id[:8]}").start()

    # timeout_s <= 0 means no wall-clock limit — keep going until the CLI exits
    # or the user cancels. Long UEFN builds (city blockouts, etc.) routinely need
    # more than 15 minutes; killing them mid-turn is worse than waiting.
    limit = float(timeout_s)
    deadline = (time.time() + limit) if limit > 0 else None
    try:
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                result.cancelled = True
                _stop(conv_id, proc)
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
        if result.stdin_writer is not None:
            with _procs_lock:
                if _writers.get(conv_id) is result.stdin_writer:
                    _writers.pop(conv_id, None)
            result.stdin_writer.close()
        t_out.join(timeout=5)
        t_err.join(timeout=2)
        # The readers are done (or gave up): close the pipes so no handle outlives the run.
        for pipe in (proc.stdout, proc.stderr, proc.stdin):
            try:
                if pipe is not None:
                    pipe.close()
            except (OSError, ValueError):
                pass
        unregister_process(conv_id, proc)

    result.returncode = proc.returncode if proc.returncode is not None else -1
    return result
