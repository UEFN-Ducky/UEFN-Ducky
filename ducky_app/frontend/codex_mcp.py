"""Merge the UEFN MCP bridge into Codex ``~/.codex/config.toml``.

Codex does not read ``mcp.json``. The same marked block the OpenAI plugin
rewrites on each ``codex exec`` launch is what Settings → Apply owns here.
A live chat may append ``--ducky-run-id`` and extra ``DUCKY_*`` env; that
still counts as up to date when command, bridge args, and base env match.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

_TOML_BEGIN = "# BEGIN UEFN-DUCKY"
_TOML_END = "# END UEFN-DUCKY"
_UEFN_TABLES = ("mcp_servers.uefn", "mcp_servers.uefn.env")
_APPROVAL_TOML = 'approval_policy = "on-failure"'
_MCP_APPROVE_TOML = 'default_tools_approval_mode = "approve"'


def _toml_quote(value: str) -> str:
    return '"' + (value or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def _strip_toml_tables(text: str, names: tuple[str, ...]) -> str:
    want = {n.lower() for n in names}
    out: list[str] = []
    skipping = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            skipping = stripped[1:-1].strip().lower() in want
        if skipping:
            continue
        out.append(line)
    return "".join(out)


def _split_marked(text: str) -> tuple[str, str | None]:
    start = text.find(_TOML_BEGIN)
    end = text.find(_TOML_END)
    if start >= 0 and end > start:
        block = text[start : end + len(_TOML_END)]
        outside = text[:start] + text[end + len(_TOML_END) :]
        return outside, block
    if start >= 0:
        return text[:start], None
    return text, None


def _join_marked(outside: str, block: str) -> str:
    if outside.strip():
        return outside.rstrip() + "\n\n" + block.lstrip("\n")
    return block if block.endswith("\n") else block + "\n"


def render_codex_block(uefn_block: dict[str, Any]) -> str:
    """Stable ``[mcp_servers.uefn]`` body (no per-chat run id)."""
    command = str(uefn_block.get("command") or "").strip()
    args = [str(a) for a in (uefn_block.get("args") or [])]
    env = uefn_block.get("env") if isinstance(uefn_block.get("env"), dict) else {}
    lines = [
        _APPROVAL_TOML,
        "",
        "[mcp_servers.uefn]",
        f"command = {_toml_quote(command)}",
        f"args = [{', '.join(_toml_quote(a) for a in args)}]",
        "enabled = true",
        "startup_timeout_sec = 60.0",
        # Codex requires a finite Duration; Ducky questions have no deadline.
        # Outstanding answers also gate all subsequent Ducky tool execution.
        "tool_timeout_sec = 1000000000000.0",
        _MCP_APPROVE_TOML,
    ]
    if env:
        lines.append("")
        lines.append("[mcp_servers.uefn.env]")
        for key in sorted(str(k) for k in env):
            lines.append(f"{key} = {_toml_quote(str(env[key]))}")
    return "\n".join(lines) + "\n"


def merge_codex_config(config_path: Path, uefn_block: dict[str, Any]) -> bool:
    """Replace the marked UEFN block. Other Codex tables stay. Returns True if written."""
    body = render_codex_block(uefn_block)
    block = f"{_TOML_BEGIN}\n{body.rstrip()}\n{_TOML_END}\n"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if config_path.is_file():
        try:
            existing = config_path.read_text(encoding="utf-8")
        except OSError:
            existing = ""
    outside, _old = _split_marked(existing)
    outside = _strip_toml_tables(outside, _UEFN_TABLES)
    new = _join_marked(outside, block)
    if new == existing:
        return False
    config_path.write_text(new, encoding="utf-8")
    return True


def _stable_args(args: Any) -> list[str]:
    out = [str(a) for a in (args or [])]
    if len(out) >= 2 and out[-2] == "--ducky-run-id":
        return out[:-2]
    return out


def _uefn_table(text: str) -> dict[str, Any] | None:
    start = text.find(_TOML_BEGIN)
    end = text.find(_TOML_END)
    chunk = text[start:end] if start >= 0 and end > start else text
    try:
        data = tomllib.loads(chunk)
    except tomllib.TOMLDecodeError:
        return None
    servers = data.get("mcp_servers")
    if not isinstance(servers, dict):
        return None
    row = servers.get("uefn")
    return row if isinstance(row, dict) else None


def codex_block_matches(config_path: Path, expected_block: dict[str, Any]) -> tuple[bool, str]:
    """True when Codex already points at this bridge.

    Extra session env and a trailing ``--ducky-run-id`` still match — chat
    launch stamps those without meaning the global bridge is stale.
    """
    if not config_path.is_file():
        return False, f"Config not found: {config_path}"
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return False, f"Could not read: {config_path}"
    actual = _uefn_table(text)
    if actual is None:
        return False, "uefn MCP server not configured"
    timeout = actual.get("tool_timeout_sec")
    if not isinstance(timeout, (int, float)) or timeout < 1_000_000_000_000:
        return False, "Out of date — question waits need the updated bridge configuration"
    if str(actual.get("command") or "") != str(expected_block.get("command") or ""):
        return False, "Out of date — click Apply"
    if _stable_args(actual.get("args")) != [str(a) for a in (expected_block.get("args") or [])]:
        return False, "Out of date — click Apply"
    expected_env = expected_block.get("env") if isinstance(expected_block.get("env"), dict) else {}
    actual_env = actual.get("env") if isinstance(actual.get("env"), dict) else {}
    for key, value in expected_env.items():
        if str(actual_env.get(key) or "") != str(value):
            return False, "Out of date — click Apply"
    return True, "Up to date"
