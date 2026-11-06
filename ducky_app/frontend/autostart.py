"""Start with Windows: Ducky opens hidden in the tray at sign-in, so clicking it later is instant.

The entry is a per-user ``Run`` value pointing at the installed EXE with ``--background``
(``Ducky-Launcher.exe`` beside it when ``native_launcher`` isn't off: :func:`start_exe`).
Only the installed app manages it: a test copy (``DUCKY_SANDBOX``), a source checkout or
any other EXE never writes or removes it, so it can never point at a throwaway copy.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "UEFN Ducky"
BACKGROUND_FLAG = "--background"
START_HIDDEN_ENV = "UEFN_DUCKY_START_HIDDEN"


def take_background_flag(argv: list[str]) -> bool:
    """Remove ``--background`` from ``argv`` in place; True when it was there."""
    found = BACKGROUND_FLAG in argv[1:]
    if found:
        argv[1:] = [a for a in argv[1:] if a != BACKGROUND_FLAG]
    return found


def started_in_background() -> bool:
    return os.environ.get(START_HIDDEN_ENV) == "1"


def command_for(exe: Path | str) -> str:
    return f'"{exe}" {BACKGROUND_FLAG}'


def start_exe(stub: Path) -> Path:
    """What Windows should start for the install root's ``UEFN-Ducky.exe`` (Run key, Open with,
    ``uefn-ducky://``): ``Ducky-Launcher.exe`` next to it when it is there and
    ``native_launcher`` isn't off, else ``stub`` itself. Every start re-registers, so a
    change of setting or a removed launcher heals the commands on the next start."""
    from frontend.native_switch import LAUNCHER_EXE_NAME

    launcher = Path(stub).parent / LAUNCHER_EXE_NAME
    if not launcher.is_file():
        return Path(stub)
    try:
        from frontend.settings import PanelSettings, normalize_native_launcher

        mode = normalize_native_launcher(PanelSettings.load().native_launcher)
    except Exception:
        return Path(stub)
    return Path(stub) if mode == "off" else launcher


def managed_exe() -> Path | None:
    """The EXE the entry may name, or None when this process must leave the entry alone."""
    if sys.platform != "win32" or os.environ.get("DUCKY_SANDBOX"):
        return None
    try:
        from frontend.bundle_root import is_packaged_runtime

        if not is_packaged_runtime():
            return None
        from frontend.app_versions import stable_exe
        from frontend.frozen_process import is_uefn_ducky_executable

        exe = Path(stable_exe()).resolve()
    except Exception:
        return None
    if not exe.is_file() or not is_uefn_ducky_executable(exe) or "setup" in exe.name.lower():
        return None
    return exe


def _winreg():
    import winreg

    return winreg


def registered_command() -> str | None:
    if sys.platform != "win32":
        return None
    reg = _winreg()
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = reg.QueryValueEx(key, VALUE_NAME)
    except OSError:
        return None
    return str(value)


def apply(enabled: bool) -> bool:
    """Write or remove the entry to match ``enabled``. True when the registry now matches."""
    exe = managed_exe()
    if exe is None:
        return False
    reg = _winreg()
    try:
        if enabled:
            command = command_for(start_exe(exe))
            if registered_command() == command:
                return True
            with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
                reg.SetValueEx(key, VALUE_NAME, 0, reg.REG_SZ, command)
            return True
        if registered_command() is None:
            return True
        with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
            reg.DeleteValue(key, VALUE_NAME)
        return True
    except OSError:
        return False


def apply_saved_setting() -> bool:
    """Match the entry to the saved setting (each launch: the EXE path may have moved)."""
    try:
        from frontend.settings import PanelSettings

        enabled = bool(getattr(PanelSettings.load(), "start_with_windows", True))
    except Exception:
        return False
    return apply(enabled)
