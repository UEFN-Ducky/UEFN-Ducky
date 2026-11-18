"""Central AppData / project paths for UEFN Ducky."""

from __future__ import annotations

import os
from pathlib import Path

APP_DATA_DIR_NAME = "UEFN-Ducky"
EXE_PREFIX = "UEFN-Ducky"


FOLDERID_PROFILE = "5E6C858F-0E22-4760-9AFE-EA3317B67173"
FOLDERID_LOCAL_APP_DATA = "F1B32785-6FBA-4FCF-9D55-7B8E7F157091"
_KF_FLAG_DONT_VERIFY = 0x4000


def local_app_data_base() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base)
    return Path.home()


def windows_known_folder(folder_id: str) -> str:
    """The shell's path for a known folder ("" off Windows or when it has none)."""
    if os.name != "nt":
        return ""
    import ctypes
    import uuid

    guid = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(folder_id).bytes_le)
    out = ctypes.c_wchar_p()
    try:
        hr = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), _KF_FLAG_DONT_VERIFY, None, ctypes.byref(out)
        )
        return (out.value or "") if hr == 0 else ""
    except OSError:
        return ""
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)


def _same_path(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b)):
        return True
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def foreign_profile_reason() -> str:
    """Why this process is not on the Windows user's own profile folders ("" = it is).

    A sandboxed test build or test harness points LOCALAPPDATA (and often
    USERPROFILE) at a scratch folder. The shell resolves the profile folder from
    the logon token, not the environment, so a mismatch is reliable. LOCALAPPDATA
    is compared second: the shell expands its ``%USERPROFILE%`` from this
    process's environment, which is only trustworthy once USERPROFILE matched.
    """
    profile = windows_known_folder(FOLDERID_PROFILE)
    if not profile:
        return ""
    if not _same_path(os.environ.get("USERPROFILE") or "", profile):
        return "USERPROFILE is not this Windows user's own profile folder"
    local = windows_known_folder(FOLDERID_LOCAL_APP_DATA)
    if local and not _same_path(os.environ.get("LOCALAPPDATA") or "", local):
        return "LOCALAPPDATA is not this Windows user's own AppData\\Local"
    return ""


def resolve_app_data_dir(*, for_write: bool = False) -> Path:
    """Return ``%LOCALAPPDATA%/UEFN-Ducky``."""
    path = local_app_data_base() / APP_DATA_DIR_NAME
    if for_write:
        path.mkdir(parents=True, exist_ok=True)
    return path


def exe_name_matches(name: str) -> bool:
    """True if filename is ``UEFN-Ducky*.exe`` but not the Setup host."""
    lower = name.lower()
    if not lower.endswith(".exe") or not lower.startswith(EXE_PREFIX.lower()):
        return False
    stem = lower[:-4]
    # Custom Setup host is published as UEFN-Ducky-Setup-<ver>.exe — the panel
    # sweep must not kill it (or Setup-engine.exe, which does not match anyway).
    if stem == "uefn-ducky-setup" or stem.startswith("uefn-ducky-setup-"):
        return False
    return True


def process_name_matches(process_name: str) -> bool:
    """Windows ``ProcessName`` without ``.exe`` suffix."""
    n = process_name.lower()
    pl = EXE_PREFIX.lower()
    if n == "uefn-ducky-setup" or n.startswith("uefn-ducky-setup-"):
        return False
    return n == pl or n.startswith(f"{pl}-") or n.startswith(f"{pl}_")


def kill_process_ps_filter() -> str:
    """PowerShell ``Where-Object`` filter for UEFN Ducky processes."""
    return (
        f"($_.ProcessName -like '{EXE_PREFIX}*' "
        f"-and $_.ProcessName -notlike '{EXE_PREFIX}-Setup*')"
    )


def stop_bridges_ps(exclude_pid: int = 0) -> str:
    """PowerShell that ends Ducky's tool bridges (``UEFN-Ducky-Bridge``, or the app exe run
    as ``bridge``), never another Ducky window: a second profile, a test bed or another
    install is not this Ducky's to close. Oct 21 2026: a test copy that quit took the
    person's Ducky down with it, with no crash trace."""
    skip = f" -and $_.ProcessId -ne {int(exclude_pid)}" if exclude_pid else ""
    return (
        f"Get-CimInstance Win32_Process -Filter \"Name like '{EXE_PREFIX}%'\" | Where-Object {{ "
        f"{bridge_ps_filter()}{skip} }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )


def bridge_ps_filter() -> str:
    """``Where-Object`` test on a Win32_Process row: a Ducky tool bridge, not a Ducky window."""
    return f"($_.Name -like '{EXE_PREFIX}-Bridge*' -or $_.CommandLine -match '\\sbridge(\\s|$)')"
