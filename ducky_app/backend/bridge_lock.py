"""Cross-process lock so multiple agent bridges serialize listener POSTs.

The in-process ``threading.Lock`` in ``backend.bridge`` only serializes tools
inside one OS process. Cursor's MCP bridge, the panel agent, and spawned
duckies are separate processes — without a machine-wide lock they all POST
in parallel and flood the listener with 503s / health-probe storms.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from types import TracebackType
from typing import Optional, Type


class CrossProcessLock:
    """Named mutex (Windows) or lock-file (elsewhere). Re-entrant within a process."""

    def __init__(self, name: str = "UEFNDuckyListenerBridge") -> None:
        self._name = name
        self._thread_lock = threading.RLock()
        self._depth = 0
        self._handle: object | None = None
        self._lock_path: str | None = None
        self._fd: int | None = None

    def acquire(self, *, timeout_sec: float = 60.0) -> bool:
        if not self._thread_lock.acquire(timeout=max(timeout_sec, 0.1)):
            return False
        if self._depth > 0:
            self._depth += 1
            return True
        ok = self._acquire_os(timeout_sec=timeout_sec)
        if not ok:
            self._thread_lock.release()
            return False
        self._depth = 1
        return True

    def release(self) -> None:
        if self._depth <= 0:
            return
        self._depth -= 1
        if self._depth == 0:
            self._release_os()
        self._thread_lock.release()

    def __enter__(self) -> "CrossProcessLock":
        if not self.acquire():
            raise TimeoutError(f"Could not acquire cross-process lock {self._name!r}")
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.release()

    def _acquire_os(self, *, timeout_sec: float) -> bool:
        if sys.platform == "win32":
            return self._acquire_win(timeout_sec=timeout_sec)
        return self._acquire_file(timeout_sec=timeout_sec)

    def _release_os(self) -> None:
        if sys.platform == "win32":
            self._release_win()
        else:
            self._release_file()

    def _acquire_win(self, *, timeout_sec: float) -> bool:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        # Local\\ — per-session is fine; all Ducky agents share the interactive session.
        mutex_name = f"Local\\{self._name}"
        handle = kernel32.CreateMutexW(None, False, mutex_name)
        if not handle:
            return False
        self._handle = handle
        WAIT_OBJECT_0 = 0
        WAIT_ABANDONED = 0x00000080
        timeout_ms = max(1, int(timeout_sec * 1000))
        result = int(kernel32.WaitForSingleObject(handle, timeout_ms))
        return result in (WAIT_OBJECT_0, WAIT_ABANDONED)

    def _release_win(self) -> None:
        if self._handle is None:
            return
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        try:
            kernel32.ReleaseMutex(self._handle)
        finally:
            kernel32.CloseHandle(self._handle)
            self._handle = None

    def _lock_file_path(self) -> str:
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(
            os.path.expanduser("~")
        )
        folder = os.path.join(base, "UEFN-Ducky")
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            pass
        return os.path.join(folder, f"{self._name}.lock")

    def _acquire_file(self, *, timeout_sec: float) -> bool:
        path = self._lock_file_path()
        self._lock_path = path
        deadline = time.time() + max(timeout_sec, 0.1)
        while True:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                self._fd = fd
                os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
                return True
            except FileExistsError:
                if time.time() >= deadline:
                    return False
                # Stale lock: if holder PID is dead, remove and retry.
                try:
                    raw = open(path, "rb").read().decode("ascii", errors="ignore").strip()
                    pid = int(raw) if raw.isdigit() else 0
                except Exception:
                    pid = 0
                if pid > 0:
                    try:
                        os.kill(pid, 0)
                    except OSError:
                        try:
                            os.unlink(path)
                            continue
                        except OSError:
                            pass
                time.sleep(0.05 + (os.getpid() % 7) * 0.01)
            except OSError:
                return False

    def _release_file(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        if self._lock_path:
            try:
                os.unlink(self._lock_path)
            except OSError:
                pass
            self._lock_path = None


# Shared singleton used by backend.bridge
LISTENER_BRIDGE_LOCK = CrossProcessLock()
