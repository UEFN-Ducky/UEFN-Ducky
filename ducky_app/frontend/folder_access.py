"""Windows Controlled folder access (Defender's ransomware protection) lets only trusted apps
write into Documents, where Fortnite Projects live. Turned on, it blocked UEFN Ducky from
saving project files until the person found Windows Security → Ransomware protection →
Allow an app through Controlled folder access (Discord reports, Nov 2026).

Shortly after a start Ducky tries one write in Documents. Blocked by Controlled folder access,
it asks once; Yes adds Ducky's own EXEs (this install and every downloaded version, by
wildcard) to the allowed apps through Windows' admin prompt, then restarts Ducky: Windows lets
an allowed app write only from its next start. Only Windows' admin prompt can add an app, so
one click on its Yes is the least it takes. Asked once per app version.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from frontend import __version__

_log = logging.getLogger(__name__)

CHECK_AFTER_S = 5.0  # after the window and the crash pop-up, never in the start's way
BLOCK = 1  # EnableControlledFolderAccess: 1 blocks writes; 0 off, 2 audit, 3/4 disk only
EXES = ("UEFN-Ducky.exe", "UEFN-Ducky-Bridge.exe")
STATE_FILE = "folder_access.json"
TITLE = "UEFN Ducky: Windows is blocking your project files"
ASK_TEXT = (
    "Windows Controlled folder access (ransomware protection) is blocking UEFN Ducky from "
    "saving into your Documents folder, where your Fortnite projects are.\n\n"
    "Allow UEFN Ducky? Windows asks for permission next, then UEFN Ducky restarts. "
    "Only UEFN Ducky is allowed; the protection stays on for every other app."
)
BY_HAND = ("Open Windows Security → Virus & threat protection → Ransomware protection → Allow an "
           "app through Controlled folder access, and add UEFN-Ducky.exe.")
NOT_ALLOWED_TEXT = "UEFN Ducky wasn't allowed, so it can't save into Documents yet.\n\nTo allow it later: " + BY_HAND
STILL_BLOCKED_TEXT = ("Windows still blocks UEFN Ducky from saving into Documents. This PC's settings may be "
                      "managed by your organization: ask whoever manages it, or " + BY_HAND[0].lower() + BY_HAND[1:])


def _state_path() -> Path:
    from frontend.app_versions import versions_dir

    return versions_dir().parent / STATE_FILE


def _read_state() -> dict[str, Any]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(answer: str) -> None:
    try:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": __version__, "answer": answer, "at": time.time()}),
                        encoding="utf-8")
    except OSError:
        pass


def write_blocked(folder: Path) -> bool:
    """True when creating a file in ``folder`` is refused (Access is denied)."""
    probe = folder / f".uefn-ducky-write-check-{os.getpid()}.tmp"
    try:
        fd = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except PermissionError:
        return True
    except OSError:
        return False  # missing folder, a leftover probe: not a block
    os.close(fd)
    try:
        probe.unlink()
    except OSError:
        pass
    return False


def protection_mode() -> int:
    """Controlled folder access's mode (0 off). Read the way Windows Security shows it."""
    import subprocess

    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-MpPreference).EnableControlledFolderAccess"],
            capture_output=True, text=True, timeout=30, creationflags=0x08000000,  # no window
        )
        return int((out.stdout or "").strip() or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def allowed_app_paths() -> list[str]:
    """Ducky's own EXEs: this one's folder, the installed one's, every downloaded version's."""
    from frontend import app_versions

    folders = [Path(sys.executable).parent]
    installed = str(app_versions.read_current().get("dir") or "")
    if installed:
        folders.append(Path(installed))
    paths = [str(folder / exe) for folder in folders for exe in EXES]
    paths += [str(app_versions.versions_dir() / "*" / exe) for exe in EXES]
    return list(dict.fromkeys(os.path.normpath(p) for p in paths))


def allow_command(paths: list[str]) -> str:
    """The admin PowerShell line, as -EncodedCommand text (no quoting through the shell)."""
    quoted = ",".join("'" + p.replace("'", "''") + "'" for p in paths)
    script = ("$ErrorActionPreference = 'Stop'; "
              f"Add-MpPreference -ControlledFolderAccessAllowedApplications {quoted}")
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def run_as_admin(encoded: str, timeout_s: float = 120.0) -> bool:
    """Windows' admin prompt for one PowerShell command. False when declined or it failed."""
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
        ]

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40  # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = "powershell.exe"
    info.lpParameters = f"-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand {encoded}"
    info.nShow = 0  # SW_HIDE
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)) or not info.hProcess:
        return False  # 1223: the person chose No
    kernel32 = ctypes.windll.kernel32
    try:
        kernel32.WaitForSingleObject(info.hProcess, int(timeout_s * 1000))
        code = wintypes.DWORD(1)
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return code.value == 0
    finally:
        kernel32.CloseHandle(info.hProcess)


def _box(text: str, yes_no: bool) -> bool:
    import ctypes

    flags = (0x4 if yes_no else 0x0) | 0x30 | 0x10000 | 0x40000  # Yes/No or OK, warning, in front
    return int(ctypes.windll.user32.MessageBoxW(None, text, TITLE, flags)) in (1, 6)


def _restart() -> None:
    from frontend import update_swap

    update_swap.restart()


def check(
    *,
    blocked: Callable[[], bool] | None = None,
    mode: Callable[[], int] = protection_mode,
    ask: Callable[[str], bool] = lambda text: _box(text, True),
    tell: Callable[[str], None] = lambda text: _box(text, False),
    allow: Callable[[list[str]], bool] = lambda paths: run_as_admin(allow_command(paths)),
    restart: Callable[[], None] = _restart,
) -> str:
    """One start's check; returns what happened (tests read it)."""
    from frontend.bundle_root import is_packaged_runtime

    # From source the writes are python.exe's, and a test copy has nobody to answer.
    if os.name != "nt" or os.environ.get("DUCKY_SANDBOX") or not is_packaged_runtime():
        return "skipped"
    state = _read_state()
    asked = state.get("version") == __version__ and str(state.get("answer") or "")
    if asked in ("no", "failed", "still_blocked"):
        return "asked"  # once per version: each try also makes Windows show its own notice
    if blocked is None:
        from frontend.ui_web.known_projects import documents_dir

        blocked = lambda: write_blocked(documents_dir())  # noqa: E731
    if not blocked():
        return "ok"
    if mode() != BLOCK:
        return "not_protection"  # refused for another reason (folder permissions)
    if asked == "allowed":
        _write_state("still_blocked")  # allowed and restarted, still refused: policy wins
        tell(STILL_BLOCKED_TEXT)
        return "still_blocked"
    if not ask(ASK_TEXT):
        _write_state("no")
        return "no"
    if not allow(allowed_app_paths()):
        _write_state("failed")
        tell(NOT_ALLOWED_TEXT)
        return "failed"
    _write_state("allowed")
    restart()
    return "allowed"


def check_soon() -> None:
    """After a start (the window's or the launcher backend's hello), on a thread of its own."""

    def run() -> None:
        time.sleep(CHECK_AFTER_S)
        try:
            _log.info("controlled folder access: %s", check())
        except Exception:
            _log.warning("controlled folder access check failed", exc_info=True)

    threading.Thread(target=run, name="folder-access-check", daemon=True).start()
