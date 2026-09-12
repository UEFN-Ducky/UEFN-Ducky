"""Build the mcpServers.uefn JSON object (whole replacement)."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from frontend.bundle_root import is_packaged_runtime
from frontend.settings import PanelSettings

# Windows MCP clients that declare an explicit ``env`` block for a stdio server
# typically REPLACE the child process's environment instead of merging it with
# the parent's. A frozen PyInstaller exe cannot boot without these — no
# SystemRoot/TEMP means the Python runtime/DLL loader fails before it can even
# open its stdio pipes, so the client just sees an instant CONNECTION_CLOSED.
_REQUIRED_ENV_KEYS = (
    "SystemRoot",
    "windir",
    "TEMP",
    "TMP",
    "APPDATA",
    "LOCALAPPDATA",
    "USERPROFILE",
    "ComSpec",
    "PATHEXT",
    "PATH",
)


def _base_os_env() -> dict[str, str]:
    """Essential Windows env vars, read from this (healthy) process's own env."""
    out: dict[str, str] = {}
    for key in _REQUIRED_ENV_KEYS:
        val = os.environ.get(key, "")
        if val:
            out[key] = val
    return out

VERSIONED_EXE_RE = re.compile(
    r"^UEFN-Ducky-(\d+)\.(\d+)\.(\d+)\.exe$",
    re.IGNORECASE,
)


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _versioned_exe_key(path: Path) -> tuple[int, int, int]:
    # One-file put the version in the file name (UEFN-Ducky-1.2.3.exe); one-dir
    # puts it on the folder (UEFN-Ducky-1.2.3/UEFN-Ducky.exe). Try both.
    for name in (path.name, f"{path.parent.name}.exe"):
        m = VERSIONED_EXE_RE.match(name)
        if m:
            return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return (0, 0, 0)


def latest_built_exe() -> Path | None:
    """Newest built panel EXE (dev Apply when not running the frozen panel).

    One-dir builds write ``dist/UEFN-Ducky-x.y.z/UEFN-Ducky.exe``; the older
    one-file builds wrote ``dist/UEFN-Ducky-x.y.z.exe``. Both are accepted so a
    half-cleaned dist/ still resolves.
    """
    dist = repo_root() / "dist"
    if not dist.is_dir():
        return None
    candidates = [
        p for p in dist.glob("UEFN-Ducky-*.exe") if p.is_file() and ".pending." not in p.name.lower()
    ]
    for folder in dist.glob("UEFN-Ducky-*"):
        inner = folder / "UEFN-Ducky.exe"
        if folder.is_dir() and inner.is_file():
            candidates.append(inner)
    if not candidates:
        for fallback in (dist / "UEFN-Ducky.exe", dist / "UEFN-Ducky" / "UEFN-Ducky.exe"):
            if fallback.is_file():
                return fallback
        return None
    return max(candidates, key=_versioned_exe_key)


def bridge_exe_for(app_exe: Path) -> Path | None:
    """The ``…-Bridge.exe`` beside *app_exe*, when this is a one-dir build.

    IDE MCP configs point at this instead of the app binary so the worker the IDE
    spawns reports its own name in Task Manager rather than being another row
    identical to the app.
    """
    candidate = app_exe.with_name(f"{app_exe.stem}-Bridge{app_exe.suffix}")
    return candidate if candidate.is_file() else None


def resolve_bridge_command() -> str:
    """
    Path written into IDE MCP configs.

    Packaged: ``sys.executable`` — whatever file you launched or renamed — except
    that a one-dir build ships a sibling ``UEFN-Ducky-Bridge.exe`` and IDEs are
    pointed at that. It runs the same code; the only difference is its own
    VERSIONINFO, so the worker an IDE spawns is identifiable in Task Manager
    instead of being a second row named exactly like the app.

    Dev: newest built exe in ``dist/``, else ``python`` + launcher.py.
    """
    if is_packaged_runtime():
        running = Path(sys.executable).resolve()
        return str(bridge_exe_for(running) or running)
    built = latest_built_exe()
    if built is not None:
        built = built.resolve()
        return str(bridge_exe_for(built) or built)
    entry = Path(__file__).resolve().parent / "launcher.py"
    if not entry.is_file():
        raise ValueError(f"frontend/launcher.py not found: {entry}")
    return sys.executable


def resolve_bridge_args(settings: PanelSettings) -> list[str]:
    port_args = ["--port", str(settings.port)]
    if is_packaged_runtime() or latest_built_exe() is not None:
        return ["bridge"] + port_args
    entry = Path(__file__).resolve().parent / "launcher.py"
    return [str(entry), "bridge"] + port_args


def build_uefn_server_block(settings: PanelSettings) -> dict:
    """Return dict suitable for mcpServers['uefn'] (stdio)."""
    env = _base_os_env()
    env["UEFN_DUCKY_PORT"] = str(settings.port)
    for key in ("UEFN_VSCODE_WORKSPACE_FOLDERS", "UEFN_DUCKY_PROJECT_ROOT"):
        val = os.environ.get(key, "")
        if val:
            env[key] = val
    root = (getattr(settings, "uefn_project_root", "") or "").strip()
    if root and not env.get("UEFN_DUCKY_PROJECT_ROOT"):
        env["UEFN_DUCKY_PROJECT_ROOT"] = root
    return {
        "type": "stdio",
        "command": resolve_bridge_command(),
        "args": resolve_bridge_args(settings),
        "env": env,
    }
