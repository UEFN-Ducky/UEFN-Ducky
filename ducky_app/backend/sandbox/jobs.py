"""Start a program inside a Windows Job Object, with its output in a capped log file.

The program starts suspended, joins the job, then resumes, so nothing it starts can slip
out of the job first. Stopping the job ends the whole tree; the job is also set to kill
everything in it when its last handle closes, which happens when Ducky's process ends.
A memory cap on the job stops a runaway program instead of letting it fill the PC.
Off Windows the program gets its own process group instead.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
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
_PROCESS_SET_QUOTA_AND_TERMINATE = 0x0100 | 0x0001


class _Job:
    """One Windows Job Object (or a no-op off Windows).

    track_only: a job that only lists its processes and kills nothing when it closes. It does
    not let programs leave: Git Bash (Cygwin) moves every program it starts out of any job
    that allows that, which hid exactly the programs it is there to find."""

    def __init__(self, memory_mb: int, *, track_only: bool = False) -> None:
        self.handle: int = 0
        if not _WIN:
            return
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

        k32 = _kernel32()
        job = k32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "CreateJobObject failed")
        info = _Extended()
        flags = 0 if track_only else _KILL_ON_JOB_CLOSE
        if memory_mb > 0:
            flags |= _LIMIT_JOB_MEMORY
            info.JobMemoryLimit = int(memory_mb) * 1024 * 1024
        info.BasicLimitInformation.LimitFlags = flags
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            k32.CloseHandle(job)
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
        self.handle = int(job)

    def assign(self, proc: subprocess.Popen) -> None:
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

    def terminate(self) -> None:
        if self.handle:
            _kernel32().TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        if self.handle:
            _kernel32().CloseHandle(self.handle)
            self.handle = 0


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
                 memory_mb: int = DEFAULT_MEMORY_MB) -> None:
        self.log = log
        self.job = _Job(memory_mb)
        flags = 0
        if _WIN:
            flags = _CREATE_SUSPENDED | _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
        log.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.proc = subprocess.Popen(
                argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, creationflags=flags, start_new_session=not _WIN,
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
        self._pump = threading.Thread(target=_pump, args=(self.proc.stdout, log), name=f"sandbox-log-{self.proc.pid}",
                                      daemon=True)
        self._pump.start()

    @property
    def pid(self) -> int:
        return int(self.proc.pid)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def tree(self) -> list[int]:
        pids = self.job.pids() if _WIN else []
        return pids or ([self.pid] if self.alive() else [])

    def stop(self, timeout_s: float = 5.0) -> int | None:
        """End the program and everything it started; frees every handle. Returns its exit code."""
        if _WIN:
            self.job.terminate()
        elif self.alive():
            try:
                os.killpg(self.proc.pid, 15)  # type: ignore[attr-defined]
            except OSError:
                pass
        try:
            code = self.proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            code = self.proc.wait(timeout=timeout_s)
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
