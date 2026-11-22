"""Apps & features' Uninstall opens UEFN Ducky's own uninstaller.

Setup installs ``Uninstall UEFN Ducky.exe`` (the Setup host under its uninstall name,
release/installer/host/Uninstall.cs) beside the app and points the uninstall entry at
it. A copy that updates itself (``frontend.update_swap``) never runs Setup again, so on
start the app copies the uninstaller its own version ships into the install folder and
points the entry there. Per-user installs only: an all-users entry and Program Files
need administrator rights, and those copies get it with their next Setup.
Inno's quiet uninstall command is left alone, so scripted uninstalls never see a window.
"""

from __future__ import annotations

import filecmp
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

EXE_NAME = "Uninstall UEFN Ducky.exe"

# Test seams: the install entry and the writer of its UninstallString.
info_override: Callable[[], dict[str, Any]] | None = None
write_override: Callable[[str], None] | None = None


def _install_info() -> dict[str, Any]:
    if info_override is not None:
        return info_override()
    from frontend.install_info import get_install_info

    return get_install_info()


def _write_uninstall_string(command: str) -> None:
    if write_override is not None:
        write_override(command)
        return
    import winreg

    from frontend.install_info import UNINSTALL_KEY

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, command)


def ensure(source_dir: Path | None = None) -> str:
    """Copy the uninstaller into the install folder and register it; returns what it did."""
    if source_dir is None:
        if sys.platform != "win32" or not getattr(sys, "frozen", False):
            return "skip: not the installed app"
        from frontend.ide_apply import auto_apply_skip_reason

        if auto_apply_skip_reason():
            return "skip: sandboxed build"
        source_dir = Path(sys.executable).parent
    info = _install_info()
    if not info.get("installed") or not info.get("install_location"):
        return "skip: not installed"
    if info.get("install_scope") != "user":
        return "skip: installed for everyone"
    src = source_dir / EXE_NAME
    if not src.is_file():
        return "skip: this build has no uninstaller"
    dest = Path(str(info["install_location"])) / EXE_NAME
    did = "ok"
    if src.resolve() != dest.resolve() and not (dest.is_file() and filecmp.cmp(src, dest, shallow=False)):
        tmp = dest.with_name(dest.name + ".new")
        shutil.copy2(src, tmp)
        os.replace(tmp, dest)
        did = "copied"
    command = f'"{dest}"'
    if (info.get("uninstall_command") or "").strip() != command:
        _write_uninstall_string(command)
        did = "registered" if did == "ok" else did + "+registered"
    return did


def linux_uninstall_command(kind: str | None = None, env: dict[str, str] | None = None) -> str:
    """Linux has no uninstall entry to point: the command that removes this copy, for Settings
    to show ("" for a source checkout: delete its folder)."""
    import shlex

    from frontend.install_info import LINUX_PACKAGE, install_kind

    env = os.environ if env is None else env
    kind = kind or install_kind(env=env)
    return {
        "appimage": f"rm {shlex.quote(env.get('APPIMAGE') or '')}",
        "deb": f"sudo apt remove {LINUX_PACKAGE}",
        "rpm": f"sudo dnf remove {LINUX_PACKAGE}",
        "aur": f"sudo pacman -R {LINUX_PACKAGE}",
        "flatpak": f"flatpak uninstall {env.get('FLATPAK_ID') or LINUX_PACKAGE}",
    }.get(kind, "")


def ensure_quietly() -> None:
    """Boot hook: never lets a failure reach the app."""
    try:
        ensure()
    except Exception:
        import logging

        logging.getLogger(__name__).debug("uninstaller link skipped", exc_info=True)
