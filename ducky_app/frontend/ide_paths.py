"""Default global MCP config paths (Windows v1)."""

from __future__ import annotations

import os
from pathlib import Path
from enum import Enum


class IdeKind(str, Enum):
    CURSOR = "cursor"
    CLAUDE = "claude"
    ANTIGRAVITY = "antigravity"
    CODEX = "codex"
    OPENCODE = "opencode"


def cursor_mcp_path() -> Path:
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".cursor" / "mcp.json"


def claude_desktop_config_path() -> Path:
    base = os.environ.get("APPDATA", "")
    if not base:
        return Path.home() / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
    return Path(base) / "Claude" / "claude_desktop_config.json"


def antigravity_mcp_path(override: str = "") -> Path:
    if override.strip():
        return Path(override)
    # Verified layout per plan / docs; see docs/antigravity_mcp.md
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".gemini" / "antigravity" / "mcp_config.json"


def codex_config_path() -> Path:
    """Codex CLI / Codex app global config (TOML, not mcp.json)."""
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".codex" / "config.toml"


def opencode_config_path() -> Path:
    """OpenCode's global config (``opencode debug paths``: ~/.config/opencode on every OS)."""
    folder = os.environ.get("OPENCODE_CONFIG_DIR", "").strip()
    if folder:
        return Path(folder) / "opencode.json"
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".config" / "opencode" / "opencode.json"


def path_for_ide(kind: IdeKind, antigravity_override: str = "") -> Path:
    if kind == IdeKind.CURSOR:
        return cursor_mcp_path()
    if kind == IdeKind.CLAUDE:
        return claude_desktop_config_path()
    if kind == IdeKind.CODEX:
        return codex_config_path()
    if kind == IdeKind.OPENCODE:
        return opencode_config_path()
    return antigravity_mcp_path(antigravity_override)
