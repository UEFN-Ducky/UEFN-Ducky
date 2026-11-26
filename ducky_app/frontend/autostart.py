"""Start with Windows: Ducky opens hidden in the tray at sign-in, so clicking it later is instant.

The entry is a per-user ``Run`` value pointing at the installed ``UEFN-Ducky.exe`` with
``--background``, never ``Ducky-Launcher.exe``: the root launcher broke after restarts 19 times
Nov 7-10 2026 ("This app can't run on your PC" at sign-in), and only a Python start runs
``install_health.check_at_start``, which puts a broken launcher back and logs what changed it.
A hidden start costs nothing visible, so the stub's 1-2 s don't matter there.
Only the installed app manages it: a test copy (``DUCKY_SANDBOX``), a source checkout or
any other EXE never writes or removes it, so it can never point at a throwaway copy.

Linux: ``~/.config/autostart/uefn-ducky.desktop`` (XDG autostart), Exec = ``$APPIMAGE`` or the
installed binary with ``--background``; the same rules keep a source run or test copy out.

macOS: ``~/Library/LaunchAgents/online.uefnducky.Ducky.plist`` (RunAtLoad), ProgramArguments =
the app bundle's ``Contents/MacOS`` executable with ``--background``; only a bundled app writes it.
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
    ``uefn-ducky://``): ``Ducky-Launcher.exe`` next to it whenever it is there (no setting
    turns it off), else ``stub`` itself. Every start re-registers, so an old command naming
    the stub heals to the launcher on the next start. A launcher that is not a whole Windows
    program counts as missing: sign-in would say "This app can't run on your PC"."""
    from frontend.install_health import starts_with_mz
    from frontend.native_switch import LAUNCHER_EXE_NAME

    launcher = Path(stub).parent / LAUNCHER_EXE_NAME
    return launcher if launcher.is_file() and starts_with_mz(launcher) else Path(stub)


def managed_exe() -> Path | None:
    """The EXE the entry may name, or None when this process must leave the entry alone."""
    if sys.platform == "darwin":
        return mac_app_exe()
    if sys.platform.startswith("linux"):
        from frontend.open_files import linux_app_exe

        found = linux_app_exe()
        return Path(found) if found else None
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


def linux_entry_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / "uefn-ducky.desktop"


def linux_entry_text(exe: Path | str) -> str:
    from frontend.open_files import desktop_exec_quote

    return ("[Desktop Entry]\nType=Application\nName=UEFN Ducky\n"
            f"Exec={desktop_exec_quote(str(exe))} {BACKGROUND_FLAG}\n"
            "Icon=uefn-ducky\nTerminal=false\nX-GNOME-Autostart-enabled=true\n")


def _apply_linux(exe: Path, enabled: bool) -> bool:
    entry = linux_entry_path()
    try:
        if not enabled:
            entry.unlink(missing_ok=True)
            return True
        text = linux_entry_text(exe)
        if not entry.is_file() or entry.read_text(encoding="utf-8") != text:
            entry.parent.mkdir(parents=True, exist_ok=True)
            entry.write_text(text, encoding="utf-8")
        return True
    except OSError:
        return False


MAC_LABEL = "online.uefnducky.Ducky"


def mac_app_exe(exe: Path | str | None = None) -> Path | None:
    """The app's own executable when it runs from a ``*.app/Contents/MacOS`` bundle (never a
    source run or test copy)."""
    if os.environ.get("DUCKY_SANDBOX"):
        return None
    path = Path(exe or sys.executable)
    parts = path.parts
    if len(parts) >= 4 and parts[-2] == "MacOS" and parts[-3] == "Contents" and parts[-4].endswith(".app"):
        return path
    return None


def mac_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"


def mac_agent_bytes(exe: Path | str) -> bytes:
    import plistlib

    return plistlib.dumps({"Label": MAC_LABEL, "ProgramArguments": [str(exe), BACKGROUND_FLAG],
                           "RunAtLoad": True, "ProcessType": "Interactive"})


def _apply_mac(exe: Path, enabled: bool) -> bool:
    # ponytail: a file in LaunchAgents is read at the next login, no launchctl load needed;
    # SMAppService (macOS 13+) when the app wants to show in Login Items as itself.
    agent = mac_agent_path()
    try:
        if not enabled:
            agent.unlink(missing_ok=True)
            return True
        data = mac_agent_bytes(exe)
        if not agent.is_file() or agent.read_bytes() != data:
            agent.parent.mkdir(parents=True, exist_ok=True)
            agent.write_bytes(data)
        return True
    except OSError:
        return False


def registered_command() -> str | None:
    if sys.platform == "darwin":
        import plistlib

        try:
            args = plistlib.loads(mac_agent_path().read_bytes()).get("ProgramArguments") or []
        except (OSError, ValueError):
            return None
        return command_for(args[0]) if args else None
    if sys.platform.startswith("linux"):
        try:
            lines = linux_entry_path().read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        return next((ln[5:] for ln in lines if ln.startswith("Exec=")), None)
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
    if sys.platform == "darwin":
        return _apply_mac(exe, enabled)
    if sys.platform.startswith("linux"):
        return _apply_linux(exe, enabled)
    reg = _winreg()
    try:
        if enabled:
            command = command_for(exe)
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
