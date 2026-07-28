"""Installed-vs-portable detection via the Inno Setup uninstall registry entry."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# NEVER change: must match MyAppId in release/installer/UEFN-Ducky.iss. A different
# GUID would make every install look "portable" and break in-place upgrades.
INNO_APP_ID = "{EAD694ED-E221-40B0-909B-AFFD7F683C9E}"

# Inno appends ``_is1`` to the AppId when it creates the uninstall key.
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{INNO_APP_ID}_is1"

_REGISTRY_VALUES = (
    "InstallLocation",
    "DisplayVersion",
    "UninstallString",
    "QuietUninstallString",
)


def _read_uninstall_entry() -> tuple[dict[str, str | None], str] | None:
    """Return ``(values, scope)`` from HKCU/HKLM (both registry views), or ``None``."""
    import winreg

    roots = ((winreg.HKEY_CURRENT_USER, "user"), (winreg.HKEY_LOCAL_MACHINE, "machine"))
    # The installer is 64-bit, but check the 32-bit view too so detection survives
    # a legacy or repackaged 32-bit Setup.
    views = (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY)
    for root, scope in roots:
        for view in views:
            try:
                key = winreg.OpenKey(root, UNINSTALL_KEY, 0, winreg.KEY_READ | view)
            except OSError:
                continue
            with key:
                values: dict[str, str | None] = {}
                for name in _REGISTRY_VALUES:
                    try:
                        values[name] = str(winreg.QueryValueEx(key, name)[0])
                    except OSError:
                        values[name] = None
                return values, scope
    return None


def _exe_under_install_location(install_location: str) -> bool:
    """
    True when the running executable lives inside the registered install folder.

    Guards against a portable EXE run from Downloads while an install also exists
    on the machine — that process must behave as portable.
    """
    try:
        exe = Path(sys.executable).resolve()
        return exe.is_relative_to(Path(install_location).resolve())
    except (OSError, ValueError):
        return False


def get_install_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "installed": False,
        "install_location": None,
        "install_scope": None,
        "registry_version": None,
        "uninstall_command": None,
        "quiet_uninstall_command": None,
    }
    if sys.platform != "win32":
        return info
    entry = _read_uninstall_entry()
    if entry is None:
        return info
    values, scope = entry
    location = (values.get("InstallLocation") or "").strip()
    if not location or not _exe_under_install_location(location):
        return info
    info.update(
        installed=True,
        install_location=location,
        install_scope=scope,
        registry_version=values.get("DisplayVersion"),
        uninstall_command=values.get("UninstallString"),
        quiet_uninstall_command=values.get("QuietUninstallString"),
    )
    return info


def is_installed() -> bool:
    return bool(get_install_info()["installed"])
