"""Build the mcpServers.uefn JSON object (whole replacement)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from frontend.bundle_root import is_packaged_runtime
from frontend.settings import PanelSettings

VERSIONED_EXE_RE = re.compile(
    r"^UEFN-Ducky-(\d+)\.(\d+)\.(\d+)\.exe$",
    re.IGNORECASE,
)


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _versioned_exe_key(path: Path) -> tuple[int, int, int]:
    m = VERSIONED_EXE_RE.match(path.name)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return (0, 0, 0)


def latest_built_exe() -> Path | None:
    """Newest ``dist/UEFN-Ducky-x.y.z.exe`` (dev Apply when not running the frozen panel)."""
    dist = repo_root() / "dist"
    if not dist.is_dir():
        return None
    candidates = [
        p
        for p in dist.glob("UEFN-Ducky-*.exe")
        if p.is_file() and ".pending." not in p.name.lower()
    ]
    if not candidates:
        fallback = dist / "UEFN-Ducky.exe"
        if fallback.is_file():
            return fallback
        return None
    return max(candidates, key=_versioned_exe_key)


def resolve_bridge_command() -> str:
    """
    Path written into IDE MCP configs.

    Packaged: always ``sys.executable`` — whatever file you launched or renamed.
  Dev: newest built exe in ``dist/``, else ``python`` + launcher.py.
    """
    if is_packaged_runtime():
        return str(Path(sys.executable).resolve())
    built = latest_built_exe()
    if built is not None:
        return str(built.resolve())
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
    env = {
        "UEFN_DUCKY_PORT": str(settings.port),
    }
    return {
        "type": "stdio",
        "command": resolve_bridge_command(),
        "args": resolve_bridge_args(settings),
        "env": env,
    }
