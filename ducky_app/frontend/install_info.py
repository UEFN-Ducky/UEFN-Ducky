"""Installed-vs-portable detection via the Inno Setup uninstall registry entry."""

from __future__ import annotations

import os
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


def installed_location() -> str:
    """The registered install folder ("" when nothing is installed)."""
    if sys.platform != "win32":
        return ""
    entry = _read_uninstall_entry()
    return (entry[0].get("InstallLocation") or "").strip() if entry else ""


def _exe_under_install_location(install_location: str) -> bool:
    """
    True when the running executable lives inside the registered install folder.

    Guards against a portable EXE run from Downloads while an install also exists
    on the machine — that process must behave as portable.
    """
    from frontend.app_versions import launcher_exe

    try:
        # A version Ducky downloaded by itself counts by the EXE that was started.
        exe = launcher_exe().resolve()
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
    if sys.platform.startswith("linux") or sys.platform == "darwin":
        kind = install_kind()
        info.update(install_kind=kind, install_label=install_label(kind))
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


# --- Linux -----------------------------------------------------------------------------

# The package name in the .deb, .rpm and AUR PKGBUILD.
LINUX_PACKAGE = "uefn-ducky"
KIND_LABELS = {
    "appimage": "AppImage", "deb": "Debian package", "rpm": "RPM package", "flatpak": "Flatpak",
    "aur": "AUR package", "source": "Source checkout", "installer": "Installed", "portable": "Portable",
    "app": "App",
}
# Package manager that owns a file -> install kind, asked in this order.
_OWNER_QUERIES = (("deb", ("dpkg", "-S")), ("rpm", ("rpm", "-qf")), ("aur", ("pacman", "-Qo")))


def run_quiet(cmd: list[str]) -> tuple[int, str]:
    """(return code, stdout); 127 when the program isn't there."""
    import subprocess

    try:
        done = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return done.returncode, done.stdout


def _running_file() -> Path:
    return Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()


def install_kind(*, env: dict[str, str] | None = None, root: str = "/", exe: Path | None = None,
                 run: Any = None) -> str:
    """How this copy was installed. Linux: 'flatpak', 'appimage', 'deb', 'rpm', 'aur' or
    'source'; macOS: 'app' (inside a ``*.app/Contents/MacOS`` bundle) or 'source'; Windows:
    'installer' or 'portable'. *env*, *root*, *exe*, *run* are test seams."""
    if sys.platform == "darwin":
        parts = Path(exe or _running_file()).parts
        in_bundle = any(parts[i].endswith(".app") and parts[i + 1:i + 3] == ("Contents", "MacOS")
                        for i in range(len(parts) - 2))
        return "app" if in_bundle else "source"
    if not sys.platform.startswith("linux"):
        return "installer" if is_installed() else "portable"
    env = os.environ if env is None else env
    if (Path(root) / ".flatpak-info").is_file():
        return "flatpak"
    appimage = env.get("APPIMAGE") or ""
    if appimage and Path(appimage).is_file():
        return "appimage"
    exe = exe or _running_file()
    # Only system folders can belong to a package; a checkout never asks the package managers.
    if str(exe).startswith(("/usr/", "/opt/")):
        run = run or run_quiet
        for kind, query in _OWNER_QUERIES:
            if run([*query, str(exe)])[0] == 0:
                return kind
    return "source"


def install_label(kind: str | None = None) -> str:
    """What Settings shows, e.g. 'AppImage · x86_64'."""
    import platform

    kind = kind or install_kind()
    return f"{KIND_LABELS.get(kind, kind)} · {platform.machine() or '?'}"
