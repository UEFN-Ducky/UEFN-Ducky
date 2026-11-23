"""UEFN under Proton / Wine on Linux: find the prefix, launch, close, restart.

Linux only; Windows keeps ``frontend.window_view``. Sources, first hit wins:
Heroic (native and Flatpak config) with its per-game Wine/Proton, then Steam
``compatdata`` prefixes that hold an Epic ``Fortnite_Studio`` manifest.

ponytail: paths map through the prefix's ``dosdevices`` links (what ``winepath``
reads) instead of spawning ``winepath`` per path; add the subprocess when a
prefix maps drives some other way. The editor is started straight through the
prefix's ``proton run`` / ``wine`` so ``-ValkyrieProject=`` reaches it
(Heroic's ``heroic://launch`` passes no editor arguments).
"""

from __future__ import annotations

import json
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from frontend.window_view import (
    _UEFN_EDITOR_EXE,
    _UEFN_SHIPPING_EXE,
    _editor_on_root,
    fortnite_studio_launch_from_item,
    uefnproject_path,
)

_HEROIC_APPS = ("Fortnite_Studio", "Fortnite")
_MANIFESTS = Path("drive_c/ProgramData/Epic/EpicGamesLauncher/Data/Manifests")
_EXE_NAMES = frozenset(n.lower() for n in (_UEFN_SHIPPING_EXE, _UEFN_EDITOR_EXE))
_NOT_FOUND = (
    "Unreal Editor for Fortnite not found in a Heroic or Steam/Proton prefix. "
    "Install Fortnite (UEFN) through Heroic, or add the Epic Games Launcher to Steam with Proton."
)


@dataclass
class UefnPrefix:
    pfx: Path  # the Wine prefix (has drive_c)
    runner: str  # "proton" | "wine"
    bin: str  # proton script or wine binary
    exe: Path  # editor exe as a Linux path
    extra: list[str] = field(default_factory=list)
    compat: Path | None = None  # STEAM_COMPAT_DATA_PATH for proton
    source: str = ""  # "heroic" | "steam"


def _home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


# --- path mapping ---------------------------------------------------------


def to_unix(pfx: Path, win_path: str) -> Path:
    """``C:\\Program Files\\X`` -> ``<pfx>/drive_c/Program Files/X``; ``Z:\\home`` -> ``/home``."""
    s = str(win_path).replace("\\", "/")
    if len(s) < 2 or s[1] != ":":
        return Path(s)
    letter, rest = s[0].lower(), s[2:].lstrip("/")
    link = pfx / "dosdevices" / f"{letter}:"
    if link.exists():
        base = link.resolve()
    elif letter == "c":
        base = pfx / "drive_c"
    elif letter == "z":
        base = Path("/")
    else:
        raise ValueError(f"drive {letter.upper()}: is not mapped in {pfx}")
    return base / rest if rest else base


def to_windows(pfx: Path, unix_path: str | os.PathLike[str]) -> str:
    """A Linux path as UEFN inside the prefix sees it (``C:`` inside drive_c, else ``Z:``)."""
    p = PurePosixPath(str(unix_path).replace("\\", "/"))
    drive_c = PurePosixPath(str(pfx).replace("\\", "/")) / "drive_c"
    try:
        rel = p.relative_to(drive_c)
        return "C:\\" + "\\".join(rel.parts)
    except ValueError:
        return "Z:" + str(p).replace("/", "\\")


# --- detection -------------------------------------------------------------


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _heroic_dirs(home: Path) -> list[Path]:
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    cands = [xdg / "heroic", home / ".var/app/com.heroicgameslauncher.hgl/config/heroic"]
    return [d for d in cands if d.is_dir()]


def _heroic_prefix(heroic: Path) -> UefnPrefix | None:
    installed = _read_json(heroic / "legendaryConfig/legendary/installed.json") or {}
    defaults = (_read_json(heroic / "config.json") or {}).get("defaultSettings") or {}
    for app in _HEROIC_APPS:
        game = installed.get(app)
        if not isinstance(game, dict):
            continue
        exe = _editor_on_root(Path(str(game.get("install_path") or "")))
        if not exe:
            continue
        cfg = (_read_json(heroic / "GamesConfig" / f"{app}.json") or {}).get(app) or {}
        wine = cfg.get("wineVersion") or defaults.get("wineVersion") or {}
        prefix = str(cfg.get("winePrefix") or defaults.get("winePrefix") or "")
        if not prefix or not wine.get("bin"):
            continue
        runner = "proton" if str(wine.get("type")) == "proton" else "wine"
        compat = Path(prefix)
        # Heroic's Proton prefix is a compatdata folder; the Wine prefix is its pfx/.
        pfx = compat / "pfx" if runner == "proton" else compat
        return UefnPrefix(
            pfx=pfx, runner=runner, bin=str(wine["bin"]), exe=Path(exe),
            compat=compat if runner == "proton" else None, source="heroic",
        )
    return None


def _steam_roots(home: Path) -> list[Path]:
    cands = [home / ".steam/steam", home / ".local/share/Steam", home / ".var/app/com.valvesoftware.Steam/data/Steam"]
    out: list[Path] = []
    for c in cands:
        if c.is_dir() and c.resolve() not in {o.resolve() for o in out}:
            out.append(c)
    return out


def _proton_for(compat: Path, steam: Path) -> str | None:
    """The Proton this compatdata was made by (``config_info``), else the newest installed."""
    try:
        for line in (compat / "config_info").read_text(encoding="utf-8").splitlines():
            if "/files/" in line:
                cand = Path(line.split("/files/", 1)[0]) / "proton"
                if cand.is_file():
                    return str(cand)
    except OSError:
        pass
    found = sorted(
        [*steam.glob("steamapps/common/Proton*/proton"), *steam.glob("compatibilitytools.d/*/proton")],
        key=lambda p: p.parent.name,
    )
    return str(found[-1]) if found else None


def _steam_prefix(steam: Path) -> UefnPrefix | None:
    for pfx in sorted(steam.glob("steamapps/compatdata/*/pfx")):
        for item in sorted((pfx / _MANIFESTS).glob("*.item")):
            try:
                parsed = fortnite_studio_launch_from_item(item.read_text(encoding="utf-8"))
            except OSError:
                continue
            if not parsed:
                continue
            exe = to_unix(pfx, parsed[0])
            proton = _proton_for(pfx.parent, steam)
            if exe.is_file() and proton:
                return UefnPrefix(
                    pfx=pfx, runner="proton", bin=proton, exe=exe, extra=parsed[1],
                    compat=pfx.parent, source="steam",
                )
    return None


def find_prefix(home: Path | None = None) -> UefnPrefix:
    home = home or _home()
    for heroic in _heroic_dirs(home):
        hit = _heroic_prefix(heroic)
        if hit:
            return hit
    for steam in _steam_roots(home):
        hit = _steam_prefix(steam)
        if hit:
            return hit
    raise RuntimeError(_NOT_FOUND)


# --- launch ---------------------------------------------------------------


def local_appdata_dirs(pre: UefnPrefix) -> list[Path]:
    """``%LOCALAPPDATA%`` of the prefix's users (``steamuser`` under Proton, ``$USER`` under Wine)."""
    return [p for p in sorted(pre.pfx.glob("drive_c/users/*/AppData/Local")) if p.parent.parent.name != "Public"]


def prefix_tool(pre: UefnPrefix, name: str) -> Path | None:
    """A tool UEFN ships beside its editor (``lore.exe``, ``urc.exe``) in FortniteGame/Binaries/Win64."""
    dirs = [pre.exe.parent]
    if len(pre.exe.parents) > 3:
        dirs.append(pre.exe.parents[3] / "FortniteGame" / "Binaries" / "Win64")
    return next((d / name for d in dirs if (d / name).is_file()), None)


def wine_command(pre: UefnPrefix, exe: str | os.PathLike[str], args: list[str]) -> tuple[list[str], dict[str, str]]:
    """argv and the extra environment that run one Windows exe in the prefix."""
    if pre.runner == "proton":
        env = {"STEAM_COMPAT_DATA_PATH": str(pre.compat or pre.pfx.parent)}
        steam = next(iter(_steam_roots(_home())), None)
        if steam:
            env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = str(steam)
        return [pre.bin, "run", str(exe), *args], env
    return [pre.bin, str(exe), *args], {"WINEPREFIX": str(pre.pfx)}


def launch_command(pre: UefnPrefix, project: str | os.PathLike[str] | None = None) -> tuple[list[str], dict[str, str]]:
    """argv and the extra environment that start the editor in its prefix."""
    args = [a for a in pre.extra if a.strip()]
    if project:
        args.append(f"-ValkyrieProject={to_windows(pre.pfx, project)}")
    return wine_command(pre, pre.exe, args)

def _start(pre: UefnPrefix, project: Path | None) -> list[str]:
    import subprocess

    cmd, env = launch_command(pre, project)
    subprocess.Popen(
        cmd, cwd=str(pre.exe.parent), env={**os.environ, **env},
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True, start_new_session=True,
    )
    return cmd


def launch_uefn() -> dict[str, Any]:
    pre = find_prefix()
    cmd = _start(pre, None)
    return {"ok": True, "exe": str(pre.exe), "path": "", "prefix": str(pre.pfx), "command": cmd}


def launch_uefn_project(root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    pre = find_prefix()
    path = uefnproject_path(root) if root else uefnproject_path()
    cmd = _start(pre, path)
    return {"ok": True, "exe": str(pre.exe), "path": str(path), "prefix": str(pre.pfx), "command": cmd}


# --- process --------------------------------------------------------------


def uefn_pids(proc: Path = Path("/proc")) -> list[int]:
    """Wine shows the Windows exe as argv[0]; match its basename, never the proton/wine wrapper."""
    pids: list[int] = []
    for d in proc.iterdir() if proc.is_dir() else ():
        if not d.name.isdigit():
            continue
        try:
            argv0 = (d / "cmdline").read_bytes().split(b"\0", 1)[0].decode("utf-8", "replace")
        except OSError:
            continue
        if argv0.replace("\\", "/").rsplit("/", 1)[-1].lower() in _EXE_NAMES:
            pids.append(int(d.name))
    return sorted(pids)


def _uefn_running() -> bool:
    return bool(uefn_pids())


def _signal_all(sig: int) -> int:
    sent = 0
    for pid in uefn_pids():
        try:
            os.kill(pid, sig)
            sent += 1
        except OSError:
            pass
    return sent


def _request_close() -> int:
    """Graceful close through the window manager; 0 when that is not available."""
    try:
        from backend.tools.core.uefn_popups import close_uefn_windows_x11
    except ImportError:
        return 0
    try:
        return int(close_uefn_windows_x11() or 0)
    except Exception:
        return 0


def _wait_exit(timeout: float) -> bool:
    try:
        from backend.tools.core.uefn_modal import auto_dismiss_save_modal
    except Exception:
        auto_dismiss_save_modal = None  # type: ignore[assignment]
    deadline = time.time() + max(0.0, float(timeout))
    while time.time() < deadline:
        if not _uefn_running():
            return True
        if auto_dismiss_save_modal is not None:
            try:
                auto_dismiss_save_modal()
            except Exception:
                pass
        time.sleep(0.4)
    return not _uefn_running()


def _kill() -> bool:
    if not _uefn_running():
        return False
    _signal_all(signal.SIGTERM)
    if _wait_exit(5.0):
        return True
    _signal_all(getattr(signal, "SIGKILL", signal.SIGTERM))
    return _wait_exit(5.0)


def close_uefn(*, timeout: float = 30.0) -> dict[str, Any]:
    """Close the windows (Save pressed if asked), then SIGTERM/SIGKILL what is left."""
    if _request_close() and _wait_exit(timeout):
        return {"ok": True, "graceful": True, "killed": False}
    return {"ok": True, "graceful": False, "killed": _kill()}


def restart_uefn_project(
    root: str | os.PathLike[str] | None = None, *, wait: bool = False, timeout: float = 180.0,
) -> dict[str, Any]:
    from frontend.window_view import wait_uefn_ready

    closed = close_uefn()
    out = launch_uefn_project(root)
    out["killed"] = bool(closed.get("killed"))
    out["graceful"] = bool(closed.get("graceful"))
    if wait:
        ready = wait_uefn_ready(timeout=timeout, root=root)
        out["ready"] = ready
        if not ready.get("ok"):
            out["ok"] = False
            out["error"] = ready.get("error") or "UEFN did not come online"
    return out
