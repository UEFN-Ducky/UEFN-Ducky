"""Merge mcpServers.uefn into IDE config files (whole-key replace)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from frontend.atomic_json import read_json_object, write_json_atomic


@dataclass
class MergePreview:
    had_existing: bool
    old_block: dict[str, Any] | None
    new_block: dict[str, Any]
    path: Path

    def conflicts(self) -> bool:
        if not self.had_existing or self.old_block is None:
            return False
        return _normalize_server_block(self.old_block) != _normalize_server_block(self.new_block)


def _normalize_server_block(block: dict[str, Any]) -> str:
    """Stable comparison JSON."""
    return json.dumps(block, sort_keys=True)


def uefn_block_matches(config_path: Path, expected_block: dict[str, Any]) -> tuple[bool, str]:
    """Return whether the IDE config's mcpServers.uefn block matches expected."""
    if not config_path.is_file():
        return False, f"Config not found: {config_path}"
    try:
        root = read_json_object(config_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False, f"Could not read: {config_path}"
    mcp_servers = root.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        return False, "No mcpServers in config"
    actual = mcp_servers.get("uefn")
    if not isinstance(actual, dict):
        return False, "uefn MCP server not configured"
    if _normalize_server_block(actual) != _normalize_server_block(expected_block):
        return False, "Out of date — click Apply"
    return True, "Up to date"


def merge_uefn_into_config(
    config_path: Path,
    uefn_block: dict[str, Any],
    *,
    dry_run: bool = False,
) -> MergePreview:
    """
    Load JSON from config_path, set root['mcpServers']['uefn'] = uefn_block (replace entire object).
    Preserves all other keys and other mcpServers entries.
    """
    root = read_json_object(config_path)
    mcp_servers = root.get("mcpServers")
    if mcp_servers is None:
        mcp_servers = {}
        root["mcpServers"] = mcp_servers
    if not isinstance(mcp_servers, dict):
        mcp_servers = {}
        root["mcpServers"] = mcp_servers

    old = mcp_servers.get("uefn")
    old_dict = old if isinstance(old, dict) else None
    preview = MergePreview(
        had_existing=old_dict is not None,
        old_block=dict(old_dict) if old_dict else None,
        new_block=dict(uefn_block),
        path=config_path,
    )

    if dry_run:
        return preview

    # Skip identical writes — rewriting mcp.json (even with the same bytes) bumps
    # mtime and Cursor reconnects the UEFN bridge ("Reloading model…" / cancels).
    if old_dict is not None and not preview.conflicts():
        return preview

    mcp_servers["uefn"] = dict(uefn_block)
    write_json_atomic(config_path, root)
    return preview
