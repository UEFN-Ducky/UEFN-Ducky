"""Write a crash dump when the app process itself crashes in native code.

``faulthandler`` (boot_report) prints the Python stacks, but a crash in .NET, a DLL or
a thread that runs no Python left nothing to say *what* crashed. This installs a
top-level exception filter (``SetUnhandledExceptionFilter``) that writes a small
minidump to ``%LOCALAPPDATA%/UEFN-Ducky/crash/ducky-<time>.dmp`` and then lets Windows
end the process as before (EXCEPTION_CONTINUE_SEARCH). The next launch's crash report
names the exception from it (``frontend/minidump.py``); the file never leaves the PC.

Crashes in WebView2 happen in msedgewebview2.exe, another process: those dumps come
from WebView2's own Crashpad, not from here.

Install it after pythonnet has loaded (.NET may set its own filter); ``start_watch``
checks every 60 s that the filter is still ours and sets it again when it isn't.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

EXCEPTION_CONTINUE_SEARCH = 0
_DUMP_FLAGS = 0x1000 | 0x40  # MiniDumpWithThreadInfo | MiniDumpWithIndirectlyReferencedMemory
WATCH_S = 60.0

_FILTER = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p) if sys.platform == "win32" else None


class _ExceptionInfo(ctypes.Structure):
    """MINIDUMP_EXCEPTION_INFORMATION (dbghelp.h packs it to 4)."""

    _pack_ = 4
    _fields_ = [("ThreadId", wintypes.DWORD), ("ExceptionPointers", ctypes.c_void_p), ("ClientPointers", wintypes.BOOL)]


# The callback must outlive everything: a collected ctypes callback is a crash inside the crash.
_installed: dict[str, object] = {"callback": None, "address": None, "previous": None, "folder": None, "watch": None}
_busy = threading.Lock()


def _dir() -> Path:
    from frontend.app_paths import resolve_app_data_dir

    return resolve_app_data_dir() / "crash"


def _kernel32() -> ctypes.WinDLL:
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.SetUnhandledExceptionFilter.restype = ctypes.c_void_p
    k.SetUnhandledExceptionFilter.argtypes = (ctypes.c_void_p,)
    k.CreateFileW.restype = wintypes.HANDLE
    k.CreateFileW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE
    )
    k.CloseHandle.argtypes = (wintypes.HANDLE,)
    k.GetCurrentProcess.restype = wintypes.HANDLE
    return k


def write_dump(path: Path, exception_pointers: int | None = None) -> bool:
    """Write a minidump of this process to *path* (with the exception, when given)."""
    k = _kernel32()
    dbghelp = ctypes.WinDLL("dbghelp")
    dbghelp.MiniDumpWriteDump.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
    )
    dbghelp.MiniDumpWriteDump.restype = wintypes.BOOL
    handle = k.CreateFileW(str(path), 0x40000000, 0, None, 2, 0x80, None)  # GENERIC_WRITE, CREATE_ALWAYS
    if not handle or handle == wintypes.HANDLE(-1).value:
        return False
    try:
        info = None
        if exception_pointers:
            info = _ExceptionInfo(k.GetCurrentThreadId(), exception_pointers, False)
        return bool(
            dbghelp.MiniDumpWriteDump(
                k.GetCurrentProcess(), k.GetCurrentProcessId(), handle, _DUMP_FLAGS,
                ctypes.byref(info) if info is not None else None, None, None,
            )
        )
    finally:
        k.CloseHandle(handle)


def _on_crash(exception_pointers: int) -> int:
    # One dump per process: a second fault (or one inside this) goes straight on to Windows.
    if not _busy.acquire(blocking=False):
        return EXCEPTION_CONTINUE_SEARCH
    try:
        folder = Path(str(_installed["folder"] or _dir()))
        folder.mkdir(parents=True, exist_ok=True)
        write_dump(folder / f"ducky-{time.strftime('%Y%m%d-%H%M%S')}.dmp", exception_pointers)
    except BaseException:
        pass
    return EXCEPTION_CONTINUE_SEARCH


def install(folder: Path | None = None) -> bool:
    """Set the filter (Windows only). Call after pythonnet loaded. True when it is set."""
    if _FILTER is None:
        return False
    try:
        if _installed["callback"] is None:
            callback = _FILTER(_on_crash)
            _installed["callback"] = callback
            _installed["address"] = ctypes.cast(callback, ctypes.c_void_p).value
        if folder is not None:
            _installed["folder"] = str(folder)
        previous = _kernel32().SetUnhandledExceptionFilter(_installed["address"])
        if previous != _installed["address"] and _installed["previous"] is None:
            _installed["previous"] = previous
        return True
    except (OSError, AttributeError):
        return False


def ensure_installed() -> bool:
    """Set ours again; True when something else had replaced it."""
    if _installed["address"] is None:
        return False
    try:
        previous = _kernel32().SetUnhandledExceptionFilter(_installed["address"])
    except OSError:
        return False
    return previous != _installed["address"]


def uninstall() -> None:
    """Put back the filter that was there first (tests)."""
    if _installed["address"] is None:
        return
    try:
        _kernel32().SetUnhandledExceptionFilter(_installed["previous"])
    except OSError:
        pass
    _installed["previous"] = None


def start_watch(every_s: float = WATCH_S) -> None:
    """Every *every_s*, make sure the filter is still ours (one daemon thread)."""
    if _installed["watch"] is not None or _installed["address"] is None:
        return

    def loop() -> None:
        while _installed["watch"] is threading.current_thread():
            time.sleep(every_s)
            if ensure_installed():
                try:
                    from frontend.error_log import record_activity

                    record_activity("crash", "Another crash handler replaced Ducky's; set it again")
                except Exception:
                    pass

    thread = threading.Thread(target=loop, daemon=True, name="native-crash-watch")
    _installed["watch"] = thread
    thread.start()
