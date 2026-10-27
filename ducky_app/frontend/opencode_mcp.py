"""Merge the UEFN MCP bridge into OpenCode's ``~/.config/opencode/opencode.json``.

OpenCode reads ``mcp.<name>`` with ``type: local`` and one ``command`` array, not
``mcpServers``. Only ``mcp.uefn`` is Ducky's; every other key stays as the person wrote it.
A file with comments (JSONC) is never rewritten: that would drop them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def render_opencode_block(uefn_block: dict[str, Any]) -> dict[str, Any]:
    env = uefn_block.get("env") if isinstance(uefn_block.get("env"), dict) else {}
    return {
        "type": "local",
        "command": [str(uefn_block.get("command") or ""), *[str(a) for a in uefn_block.get("args") or []]],
        "environment": {str(k): str(v) for k, v in env.items()},
        "enabled": True,
    }


def _read(config_path: Path) -> dict[str, Any]:
    if not config_path.is_file():
        return {}
    text = config_path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    data = json.loads(text)  # JSONC raises: refuse rather than lose the person's comments
    if not isinstance(data, dict):
        raise ValueError(f"{config_path} is not a JSON object")
    return data


def merge_opencode_config(config_path: Path, uefn_block: dict[str, Any]) -> bool:
    """Set ``mcp.uefn``; keep everything else. Returns True when the file changed."""
    try:
        data = _read(config_path)
    except ValueError as exc:
        raise ValueError(f"Could not update {config_path} (has comments or is not plain JSON): {exc}") from None
    block = render_opencode_block(uefn_block)
    mcp = data.get("mcp") if isinstance(data.get("mcp"), dict) else {}
    if mcp.get("uefn") == block:
        return False
    data.setdefault("$schema", "https://opencode.ai/config.json")
    data["mcp"] = {**mcp, "uefn": block}
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = config_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, config_path)
    return True


def opencode_block_matches(config_path: Path, expected_block: dict[str, Any]) -> tuple[bool, str]:
    """True when OpenCode already points at this bridge (a trailing ``--ducky-run-id`` still matches)."""
    if not config_path.is_file():
        return False, f"Config not found: {config_path}"
    try:
        data = _read(config_path)
    except (OSError, ValueError):
        return False, f"Could not read: {config_path}"
    actual = (data.get("mcp") or {}).get("uefn") if isinstance(data.get("mcp"), dict) else None
    if not isinstance(actual, dict):
        return False, "uefn MCP server not configured"
    command = [str(c) for c in actual.get("command") or []]
    if len(command) >= 2 and command[-2] == "--ducky-run-id":
        command = command[:-2]
    if command != render_opencode_block(expected_block)["command"] or actual.get("enabled") is False:
        return False, "Out of date — click Apply"
    return True, "OK"
