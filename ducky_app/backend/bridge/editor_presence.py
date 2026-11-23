"""Is an editor running on this PC? Read from the process list, never from a socket.

With UEFN closed Ducky must open no connection to the listener port or Epic's MCP port:
background readers (the panel's status, an agent turn's prompt, health checks) and tool calls
alike ask :func:`uefn_running` / :func:`editor_running` first, and a closed editor answers
"offline" without a connect. The process list is read at most once per
:data:`CACHE_SEC` per process; ``UEFN_DUCKY_ALWAYS_PROBE_LISTENER=1`` turns the check off
(a listener served by something other than a local editor). Without ``psutil`` the answer
is True, so a missing package never hides a running editor.
"""

from __future__ import annotations

import os
import sys
import threading
import time

CACHE_SEC = 5.0
ALWAYS_PROBE_ENV = "UEFN_DUCKY_ALWAYS_PROBE_LISTENER"
_UEFN_PREFIX = "unrealeditorfortnite"
_UNREAL_PREFIX = "unrealeditor"

_lock = threading.Lock()
_cache: dict[str, float | bool] = {}
_mono = time.monotonic


def _toolhelp_names() -> list[str] | None:
    """Every process's image name from one Toolhelp snapshot (Windows), None when it fails.

    psutil reads a Windows process name by opening the process for its exe path: ~20 ms
    each, so ~400 processes took 8-12 s (Nov 4 2026). The snapshot lists them all at once.
    """
    import ctypes
    from ctypes import wintypes

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
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return None
    names: list[str] = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.szExeFile:
                names.append(entry.szExeFile.lower())
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return names or None


def _process_names() -> list[str] | None:
    """Lower-case image names of every running process, or None when they can't be read."""
    if sys.platform == "win32":
        try:
            names = _toolhelp_names()
        except Exception:
            names = None
        if names is not None:
            return names
    try:
        import psutil
    except ImportError:
        return None
    names: list[str] = []
    try:
        for proc in psutil.process_iter(["name"]):
            name = proc.info.get("name")
            if name:
                names.append(str(name).lower())
    except Exception:
        return None
    return names


def _scan() -> tuple[bool, bool]:
    """(UEFN running, any Unreal editor running)."""
    names = _process_names()
    if names is None:
        return True, True
    uefn = any(n.startswith(_UEFN_PREFIX) for n in names)
    return uefn, uefn or any(n.startswith(_UNREAL_PREFIX) for n in names)


def _state() -> tuple[bool, bool]:
    if (os.environ.get(ALWAYS_PROBE_ENV) or "").strip() in ("1", "true", "yes"):
        return True, True
    with _lock:
        now = _mono()
        if _cache and now - float(_cache["at"]) < CACHE_SEC:
            return bool(_cache["uefn"]), bool(_cache["any"])
        uefn, any_editor = _scan()
        _cache.update(at=now, uefn=uefn, any=any_editor)
        return uefn, any_editor


def uefn_running() -> bool:
    """True when a UEFN process (``UnrealEditorFortnite*``) runs on this PC."""
    return _state()[0]


def editor_running() -> bool:
    """True when UEFN or Unreal Editor runs on this PC (Epic's MCP lives in either)."""
    return _state()[1]


def forget() -> None:
    """Read the process list again on the next ask (UEFN was just launched or closed)."""
    with _lock:
        _cache.clear()
    with _link_lock:
        _link.clear()


# Off Windows with no local UEFN, editor calls can run on another UEFN Ducky of the
# account (a Windows PC) over the sealed link (frontend.ducky_link). Windows runs UEFN
# itself, so there it is always None and nothing here runs.
LINK_CACHE_SEC = 30.0
# Pins :func:`route` (local | proton | remote | none): test sessions and their daemons set it
# so the route never asks the account or the disk.
ROUTE_ENV = "UEFN_DUCKY_UEFN_ROUTE"
_ROUTES = ("local", "proton", "remote", "none")
_link_lock = threading.Lock()
_link: dict[str, object] = {}


def linked_pc() -> dict | None:
    """``{"keyId", "name"}`` of the live UEFN Ducky that runs this one's editor calls, else None.

    ponytail: the first live Ducky of the account that isn't this one; a picker setting if
    an account ever links several Windows PCs.
    """
    if sys.platform == "win32" or _forced_route() not in ("", "remote") or uefn_running():
        return None
    with _link_lock:
        now = _mono()
        if "at" in _link and now - float(_link["at"]) < LINK_CACHE_SEC:  # type: ignore[arg-type]
            return _link["pc"]  # type: ignore[return-value]
        try:
            from frontend.ducky_link import list_duckies

            rows = list_duckies().get("devices") or []
        except Exception:
            rows = []
        pc = next(
            ({"keyId": str(d["keyId"]), "name": str(d.get("name") or "your other PC")}
             for d in rows if isinstance(d, dict) and d.get("live") and not d.get("mine") and d.get("keyId")),
            None,
        )
        _link.update(at=now, pc=pc)
        return pc


def _forced_route() -> str:
    forced = (os.environ.get(ROUTE_ENV) or "").strip().lower()
    return forced if forced in _ROUTES else ""


def proton_uefn() -> bool:
    """Off Windows: True when UEFN is installed in a Heroic or Steam/Proton prefix."""
    if sys.platform == "win32":
        return False
    with _link_lock:
        now = _mono()
        if "proton_at" in _link and now - float(_link["proton_at"]) < LINK_CACHE_SEC:  # type: ignore[arg-type]
            return bool(_link["proton"])
    try:
        from backend.tools.uefn.proton import find_prefix

        find_prefix()
        found = True
    except Exception:
        found = False
    with _link_lock:
        _link.update(proton_at=now, proton=found)
    return found


def route() -> str:
    """Where editor calls go: ``local`` (UEFN running here, Windows always), ``proton``
    (UEFN installed under Proton here), ``remote`` (a linked PC) or ``none``."""
    if sys.platform == "win32":
        return "local"
    forced = _forced_route()
    if forced:
        return forced
    if uefn_running():
        return "local"
    if proton_uefn():
        return "proton"
    return "remote" if linked_pc() else "none"
