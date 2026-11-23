"""Linux: find Blender, Godot and Unreal Editor installs.

Each hit is ``{"app", "kind", "argv", "path", "version"}``; ``argv`` is what starts it
(``["flatpak", "run", "org.blender.Blender"]`` for a Flatpak). Order: PATH, Flatpak, Steam,
/opt (and the home folder for Unreal source builds). Nothing here runs the app itself.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

OPT = Path("/opt")

_APPS: dict[str, dict[str, Any]] = {
    "blender": {"names": ["blender"], "flatpak": "org.blender.Blender",
                "steam": "Blender/blender", "opt": ["blender*/blender"]},
    "godot": {"names": ["godot4", "godot", "Godot"], "flatpak": "org.godotengine.Godot",
              "steam": "Godot Engine/godot.x11.opt.tools.64", "opt": ["godot*/godot*"]},
    "unreal": {"names": ["UnrealEditor"], "flatpak": "",
               "steam": "", "opt": ["UnrealEngine*/Engine/Binaries/Linux/UnrealEditor",
                                    "UE_*/Engine/Binaries/Linux/UnrealEditor"],
               "home": ["UnrealEngine*/Engine/Binaries/Linux/UnrealEditor",
                        "Epic/UE_*/Engine/Binaries/Linux/UnrealEditor"]},
}

_VER = re.compile(r"(\d+\.\d+(?:\.\d+)?)")


def _version(text: str) -> str:
    m = _VER.search(text)
    return m.group(1) if m else ""


def _flatpak_info(app_id: str) -> str:
    """``flatpak info`` output, or '' when Flatpak or the app isn't installed."""
    flatpak = shutil.which("flatpak")
    if not flatpak:
        return ""
    try:
        out = subprocess.run([flatpak, "info", app_id], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout if out.returncode == 0 else ""


def _steam_roots(home: Path) -> list[Path]:
    return [home / ".local/share/Steam/steamapps/common", home / ".steam/steam/steamapps/common",
            home / ".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/common"]


def _newest(paths: list[Path]) -> list[Path]:
    key = lambda p: [int(x) for x in re.findall(r"\d+", str(p))]  # noqa: E731
    return sorted((p for p in paths if p.is_file()), key=key, reverse=True)


def find_all(app: str, home: str | Path | None = None) -> list[dict[str, Any]]:
    spec = _APPS[app]
    h = Path(home or Path.home())
    hits: list[dict[str, Any]] = []

    def add(kind: str, argv: list[str], path: str, version: str = "") -> None:
        if all(x["path"] != path for x in hits):
            hits.append({"app": app, "kind": kind, "argv": argv, "path": path,
                         "version": version or _version(path)})

    for name in spec["names"]:
        found = shutil.which(name)
        if found:
            add("path", [found], str(Path(found).resolve()))
    if spec["flatpak"]:
        info = _flatpak_info(spec["flatpak"])
        if info:
            ver = re.search(r"^\s*Version:\s*(\S+)", info, re.M)
            add("flatpak", ["flatpak", "run", spec["flatpak"]], f"flatpak:{spec['flatpak']}",
                _version(ver.group(1)) if ver else "")
    if spec["steam"]:
        for root in _steam_roots(h):
            exe = root / spec["steam"]
            if exe.is_file():
                add("steam", [str(exe)], str(exe))
    for kind, base, globs in (("opt", OPT, spec["opt"]), ("home", h, spec.get("home", []))):
        for pattern in globs:
            for exe in _newest(list(base.glob(pattern))):
                add(kind, [str(exe)], str(exe))
    return hits


def find(app: str, home: str | Path | None = None) -> dict[str, Any] | None:
    """The first install of ``app`` ("blender", "godot", "unreal"), or None."""
    hits = find_all(app, home)
    return hits[0] if hits else None
