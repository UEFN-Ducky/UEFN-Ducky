"""Central AppData / project paths for UEFN Ducky."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DATA_DIR_NAME = "UEFN-Ducky"
EXE_PREFIX = "UEFN-Ducky"


FOLDERID_PROFILE = "5E6C858F-0E22-4760-9AFE-EA3317B67173"
FOLDERID_LOCAL_APP_DATA = "F1B32785-6FBA-4FCF-9D55-7B8E7F157091"
_KF_FLAG_DONT_VERIFY = 0x4000


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _is_mac() -> bool:
    return sys.platform == "darwin"


def _test_isolated_base() -> Path | None:
    # ponytail: under the test conftest (which sets DUCKY_TESTS_ISOLATED_APPDATA) a test that
    # repoints only LOCALAPPDATA is followed, as on Windows; a real Linux/macOS install never has it.
    if os.environ.get("DUCKY_TESTS_ISOLATED_APPDATA") and os.path.isabs(os.environ.get("LOCALAPPDATA") or ""):
        return Path(os.environ["LOCALAPPDATA"])
    return None


def _xdg(var: str, default: str) -> Path:
    # XDG spec: relative values are invalid and must be ignored.
    value = os.environ.get(var) or ""
    return Path(value) if os.path.isabs(value) else Path.home() / default


def local_app_data_base() -> Path:
    """Base folder for app data: ``%LOCALAPPDATA%`` on Windows, ``$XDG_DATA_HOME`` on Linux,
    ``~/Library/Application Support`` on macOS."""
    if _is_linux() or _is_mac():
        isolated = _test_isolated_base()
        if isolated is not None:
            return isolated
        if _is_mac():
            return Path.home() / "Library" / "Application Support"
        return _xdg("XDG_DATA_HOME", ".local/share")
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base)
    return Path.home()


def cache_base() -> Path:
    """Base folder for caches (``$XDG_CACHE_HOME`` on Linux, ``~/Library/Caches`` on macOS;
    same as data on Windows)."""
    if _is_mac():
        isolated = _test_isolated_base()
        return isolated if isolated is not None else Path.home() / "Library" / "Caches"
    if _is_linux():
        return _xdg("XDG_CACHE_HOME", ".cache")
    return local_app_data_base()


def config_base() -> Path:
    """Base folder for config (``$XDG_CONFIG_HOME`` on Linux; same as data on Windows and macOS)."""
    if _is_linux():
        return _xdg("XDG_CONFIG_HOME", ".config")
    return local_app_data_base()


def logs_dir() -> Path:
    """Folder for log and crash files: ``~/Library/Logs/UEFN-Ducky`` on macOS,
    ``<app data>/UEFN-Ducky/logs`` elsewhere."""
    if _is_mac():
        isolated = _test_isolated_base()
        if isolated is None:
            return Path.home() / "Library" / "Logs" / APP_DATA_DIR_NAME
    return resolve_app_data_dir() / "logs"


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


def stop_bridges_ps() -> str:
    """PowerShell that ends this Ducky's tool bridges (``UEFN-Ducky-Bridge``, or the app exe run
    as ``bridge``), never another Ducky window: a second profile, a test bed or another
    install is not this Ducky's to close. Oct 21 2026: a test copy that quit took the
    person's Ducky down with it, with no crash trace."""
    return (
        f"Get-CimInstance Win32_Process -Filter \"Name like '{EXE_PREFIX}%'\" | Where-Object {{ "
        f"{bridge_ps_filter()} }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )


def bridge_ps_filter() -> str:
    """``Where-Object`` test on a Win32_Process row: one of THIS Ducky's tool bridges, not a Ducky
    window and not another Ducky's bridge. A bridge carries the port of the Ducky it serves
    (mcp_block.resolve_bridge_args); until Nov 2026 any Ducky that quit ended every Ducky's
    bridges on the PC, so a test copy closing took the person's own tools with it."""
    from frontend.settings import UEFN_LISTENER_PORT

    return (
        f"(($_.Name -like '{EXE_PREFIX}-Bridge*' -or $_.CommandLine -match '\\sbridge(\\s|$)') "
        f"-and $_.CommandLine -match '--port\\s+{int(UEFN_LISTENER_PORT)}(\\s|$)')"
    )
