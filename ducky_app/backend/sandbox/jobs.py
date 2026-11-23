"""Start a program inside a Windows Job Object, with its output in a capped log file.

The program starts suspended, joins the job, then resumes, so nothing it starts can slip
out of the job first. Stopping the job ends the whole tree; the job is also set to kill
everything in it when its last handle closes, which happens when Ducky's process ends.
A memory cap on the job stops a runaway program instead of letting it fill the PC.
Off Windows the program gets its own session (process group = its pid) instead: stopping kills
the group, PR_SET_PDEATHSIG (Linux) ends it with Ducky, and RLIMIT_AS caps its memory.
"""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

LOG_CAP_BYTES = 2 * 1024 * 1024  # per log file; one older file is kept beside it
DEFAULT_MEMORY_MB = 4096
_WIN = sys.platform == "win32"
_CREATE_SUSPENDED = 0x00000004
_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_KILL_ON_JOB_CLOSE = 0x2000
_LIMIT_JOB_MEMORY = 0x0200
_LIMIT_PRIORITY_CLASS = 0x0020
_BELOW_NORMAL_PRIORITY_CLASS = 0x4000
_IDLE_PRIORITY_CLASS = 0x0040
_EXTENDED_LIMIT_INFO = 9
_PROCESS_SET_QUOTA_AND_TERMINATE = 0x0100 | 0x0001
# Agent terminals' and sandbox programs' jobs: they share one memory budget
# (frontend.ui_web.terminal.resource_guard.rebalance), so together they leave room for UEFN.
_shared: "weakref.WeakSet[_Job]" = weakref.WeakSet()
_shared_lock = threading.Lock()
_PR_SET_PDEATHSIG = 1
_spawn_pool: ThreadPoolExecutor | None = None
_spawn_lock = threading.Lock()


def _prctl() -> Any:
    """libc prctl, resolved in the parent (never in a forked child). None off Linux."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        fn = ctypes.CDLL(None, use_errno=True).prctl
        fn.argtypes = (ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong)
        fn.restype = ctypes.c_int
        return fn
    except (OSError, AttributeError):
        return None


def _posix_preexec(memory_mb: int, below_normal: bool) -> Any:
    """The child-side setup: die with Ducky, cap memory, lower priority. Runs after fork, so it
    only calls what the parent already resolved and never raises."""
    prctl = _prctl()
    parent = os.getpid()
    cap = max(0, int(memory_mb)) * 1024 * 1024

    def run() -> None:
        if prctl is not None:
            prctl(_PR_SET_PDEATHSIG, int(signal.SIGKILL), 0, 0, 0)
            if os.getppid() != parent:  # Ducky died before prctl took hold
                os._exit(1)
        if cap:
            import resource

            # ponytail: RLIMIT_AS is per process and counts virtual memory, not the tree's RSS like
            # a job's cap; a cgroup (systemd-run --user --scope -p MemoryMax) if that falls short.
            try:
                resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
            except (ValueError, OSError):
                pass
        if below_normal:
            try:
                os.nice(10)
            except OSError:
                pass

    return run


def posix_popen(argv: list[str], *, memory_mb: int = 0, below_normal: bool = False, **kwargs: Any) -> subprocess.Popen:
    """Popen off Windows: own session (so os.killpg(pid) ends the whole tree), killed when Ducky
    dies, memory capped at memory_mb (0: none).

    PR_SET_PDEATHSIG fires when the *thread* that forked exits, not the process, so every child
    is forked from one long-lived spawner thread instead of the caller's (often short) thread."""
    global _spawn_pool
    kwargs["start_new_session"] = True
    kwargs["preexec_fn"] = _posix_preexec(memory_mb, below_normal)
    with _spawn_lock:
        if _spawn_pool is None:
            _spawn_pool = ThreadPoolExecutor(1, thread_name_prefix="posix-spawn")
    return _spawn_pool.submit(subprocess.Popen, argv, **kwargs).result()


def kill_group(pgid: int, sig: int = 0) -> bool:
    """Signal a POSIX process group (default SIGKILL). False when it is already gone."""
    try:
        os.killpg(int(pgid), sig or signal.SIGKILL)  # type: ignore[attr-defined]
        return True
    except (OSError, AttributeError):
        return False


def share_budget(job: "_Job") -> None:
    """Count this job in the shared memory budget until it closes."""
    if job.handle and job.memory_mb > 0:
        with _shared_lock:
            _shared.add(job)


def shared_jobs() -> list["_Job"]:
    with _shared_lock:
        return [job for job in _shared if job.handle]


class _Job:
    """One Windows Job Object (or a no-op off Windows).

    track_only: a job that only lists its processes and kills nothing when it closes. It does
    not let programs leave: Git Bash (Cygwin) moves every program it starts out of any job
    that allows that, which hid exactly the programs it is there to find."""

    def __init__(self, memory_mb: int, *, track_only: bool = False, below_normal: bool = False) -> None:
        """memory_mb > 0 caps what every process in the job holds together; below_normal runs
        them all at below-normal priority, so the PC's own apps stay responsive."""
        self.handle: int = 0
        self.memory_mb = max(0, int(memory_mb))
        self._below_normal = bool(below_normal)
        self._track_only = bool(track_only)
        self.pgid = 0  # off Windows: the process group assign() took in
        # The most the shared budget may give it (0: no ceiling).
        self.ceiling_mb = 0
        if not _WIN:
            return
        k32 = _kernel32()
        job = k32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "CreateJobObject failed")
        info = _extended_info()()
        flags = 0 if track_only else _KILL_ON_JOB_CLOSE
        if memory_mb > 0:
            flags |= _LIMIT_JOB_MEMORY
            info.JobMemoryLimit = int(memory_mb) * 1024 * 1024
        if below_normal:
            flags |= _LIMIT_PRIORITY_CLASS
            info.BasicLimitInformation.PriorityClass = _BELOW_NORMAL_PRIORITY_CLASS
        info.BasicLimitInformation.LimitFlags = flags
        if not k32.SetInformationJobObject(job, _EXTENDED_LIMIT_INFO, ctypes.byref(info), ctypes.sizeof(info)):
            k32.CloseHandle(job)
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
        self.handle = int(job)

    def set_memory_limit(self, mb: int) -> bool:
        """Change the job's memory cap while its processes run (other limits stay). Lowered
        below what they hold, their next allocations are refused; nothing is killed."""
        mb = max(1, int(mb))
        if sys.platform.startswith("linux") and self.pgid:
            import resource

            cap = mb * 1024 * 1024
            done = False
            for pid in self.pids():
                try:  # ponytail: raising past a lowered hard limit needs CAP_SYS_RESOURCE
                    resource.prlimit(pid, resource.RLIMIT_AS, (cap, cap))
                    done = True
                except (OSError, ValueError):
                    pass
            self.memory_mb = mb
            return done
        if not self.handle:
            self.memory_mb = mb
            return False
        info = self._query()
        if info is None:
            return False
        info.BasicLimitInformation.LimitFlags |= _LIMIT_JOB_MEMORY
        info.JobMemoryLimit = mb * 1024 * 1024
        if not _kernel32().SetInformationJobObject(self.handle, _EXTENDED_LIMIT_INFO, ctypes.byref(info),
                                                    ctypes.sizeof(info)):
            return False
        self.memory_mb = mb
        return True

    def set_idle(self, idle: bool) -> bool:
        """Run the job's processes at idle priority (True) or back at what it was made with."""
        if not _WIN and self.pgid:
            # Back from idle (nice 19) needs CAP_SYS_NICE, so unprivileged that part returns False.
            try:
                os.setpriority(os.PRIO_PGRP, self.pgid, 19 if idle else (10 if self._below_normal else 0))
                return True
            except OSError:
                return False
        if not self.handle:
            return False
        info = self._query()
        if info is None:
            return False
        basic = info.BasicLimitInformation
        if idle:
            basic.LimitFlags |= _LIMIT_PRIORITY_CLASS
            basic.PriorityClass = _IDLE_PRIORITY_CLASS
        elif self._below_normal:
            basic.LimitFlags |= _LIMIT_PRIORITY_CLASS
            basic.PriorityClass = _BELOW_NORMAL_PRIORITY_CLASS
        else:
            basic.LimitFlags &= ~_LIMIT_PRIORITY_CLASS
            basic.PriorityClass = 0
        return bool(_kernel32().SetInformationJobObject(self.handle, _EXTENDED_LIMIT_INFO, ctypes.byref(info),
                                                         ctypes.sizeof(info)))

    def assign(self, proc: subprocess.Popen) -> None:
        if not _WIN:
            self.pgid = int(proc.pid)  # posix_popen / start_new_session: pgid == pid
            return
        if self.handle and not _kernel32().AssignProcessToJobObject(self.handle, int(proc._handle)):  # type: ignore[attr-defined]
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")

    def assign_pid(self, pid: int) -> bool:
        """Put a running process in the job; what it starts from now on joins too."""
        if not self.handle:
            return False
        k32 = _kernel32()
        proc = k32.OpenProcess(_PROCESS_SET_QUOTA_AND_TERMINATE, False, int(pid))
        if not proc:
            return False
        try:
            return bool(k32.AssignProcessToJobObject(self.handle, proc))
        finally:
            k32.CloseHandle(proc)

    def pids(self) -> list[int]:
        """Every process in the job right now (the program and what it started)."""
        if not _WIN and self.pgid:
            from backend.util.proc_table import group

            return group(self.pgid)
        if not self.handle:
            return []
        from ctypes import wintypes

        class _List(ctypes.Structure):
            _fields_ = [("Assigned", wintypes.DWORD), ("InList", wintypes.DWORD),
                        ("Ids", ctypes.c_size_t * 512)]

        info = _List()
        if not _kernel32().QueryInformationJobObject(self.handle, 3, ctypes.byref(info), ctypes.sizeof(info), None):
            return []
        return [int(info.Ids[i]) for i in range(min(int(info.InList), 512))]

    def limits(self) -> tuple[int, int, int]:
        """(limit flags, priority class, memory limit MB) as Windows holds them for the job."""
        info = self._query()
        if info is None:
            return 0, 0, 0
        basic = info.BasicLimitInformation
        return int(basic.LimitFlags), int(basic.PriorityClass), int(info.JobMemoryLimit // (1024 * 1024))

    def peak_mb(self) -> int:
        """The most memory the job's processes ever held at once, in MB (0 when unknown)."""
        info = self._query()
        return int(info.PeakJobMemoryUsed // (1024 * 1024)) if info is not None else 0

    def _query(self) -> Any:
        if not self.handle:
            return None
        info = _extended_info()()
        if not _kernel32().QueryInformationJobObject(self.handle, _EXTENDED_LIMIT_INFO, ctypes.byref(info),
                                                     ctypes.sizeof(info), None):
            return None
        return info

    def terminate(self) -> None:
        if not _WIN and self.pgid:
            kill_group(self.pgid)
        if self.handle:
            _kernel32().TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        with _shared_lock:
            _shared.discard(self)
        if not _WIN and self.pgid:
            if not self._track_only:  # what KILL_ON_JOB_CLOSE does on Windows
                kill_group(self.pgid)
            self.pgid = 0
        if self.handle:
            _kernel32().CloseHandle(self.handle)
            self.handle = 0


_extended: Any = None


def _extended_info() -> Any:
    """The JOBOBJECT_EXTENDED_LIMIT_INFORMATION structure type."""
    global _extended
    if _extended is None:
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

        _extended = _Extended
    return _extended


_k32: Any = None


def _kernel32() -> Any:
    global _k32
    if _k32 is None:
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
        k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        k32.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                                  ctypes.c_void_p)
        k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        _k32 = k32
    return _k32


def _resume(proc: subprocess.Popen) -> None:
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = (ctypes.c_void_p,)
    ntdll.NtResumeProcess(ctypes.c_void_p(int(proc._handle)))  # type: ignore[attr-defined]


def _pump(stream: Any, log: Path) -> None:
    """Copy a program's output into ``log``; past LOG_CAP_BYTES it starts a fresh file and
    keeps the previous one as ``.old``, so a chatty program never fills the disk or memory."""
    written = 0
    out = open(log, "ab")
    try:
        while True:
            chunk = stream.read1(65536) if hasattr(stream, "read1") else stream.read(65536)
            if not chunk:
                break
            if written + len(chunk) > LOG_CAP_BYTES:
                out.close()
                os.replace(log, log.with_suffix(log.suffix + ".old"))
                out = open(log, "ab")
                written = 0
            out.write(chunk)
            out.flush()
            written += len(chunk)
    except (OSError, ValueError):
        pass
    finally:
        out.close()
        try:
            stream.close()
        except OSError:
            pass


class Launched:
    """A program started in its own job, its output going to ``log``."""

    def __init__(self, argv: list[str], *, cwd: str, env: dict[str, str], log: Path,
                 memory_mb: int = DEFAULT_MEMORY_MB, share: bool = False) -> None:
        """share: an agent's program; its cap follows the shared budget (share_budget)."""
        self.log = log
        self.job = _Job(memory_mb)
        flags = 0
        if _WIN:
            flags = _CREATE_SUSPENDED | _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
        log.parent.mkdir(parents=True, exist_ok=True)
        try:
            if _WIN:
                self.proc = subprocess.Popen(
                    argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, creationflags=flags, start_new_session=not _WIN,
                )
            else:
                self.proc = posix_popen(
                    argv, memory_mb=self.job.memory_mb, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                )
        except Exception:
            self.job.close()
            raise
        try:
            self.job.assign(self.proc)
        except Exception:
            self.proc.kill()
            self.job.close()
            raise
        if _WIN:
            _resume(self.proc)
        if share:
            share_budget(self.job)
        self._pump = threading.Thread(target=_pump, args=(self.proc.stdout, log), name=f"sandbox-log-{self.proc.pid}",
                                      daemon=True)
        self._pump.start()

    @property
    def pid(self) -> int:
        return int(self.proc.pid)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def tree(self) -> list[int]:
        pids = self.job.pids()
        return pids or ([self.pid] if self.alive() else [])

    def stop(self, timeout_s: float = 5.0) -> int | None:
        """End the program and everything it started; frees every handle. Returns its exit code."""
        if _WIN:
            self.job.terminate()
        elif self.alive():
            kill_group(self.proc.pid, signal.SIGTERM)
        try:
            code = self.proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            code = self.proc.wait(timeout=timeout_s)
        if not _WIN:
            kill_group(self.proc.pid)  # anything left in the group after SIGTERM
        self._pump.join(timeout=timeout_s)
        self.job.close()
        return code


def read_tail(log: Path, max_chars: int = 6000) -> str:
    """The end of a program's output (the previous file too when the current one is short)."""
    chunks: list[bytes] = []
    for path in (log.with_suffix(log.suffix + ".old"), log):
        try:
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - max_chars * 2))
                chunks.append(fh.read())
        except OSError:
            continue
    text = b"".join(chunks).decode("utf-8", "replace")
    return text[-max_chars:]
