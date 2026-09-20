"""Prompt block for Store desktop plugins (blender_*, etc.) with live readiness."""

from __future__ import annotations

import socket
from typing import Any


def _tcp_open(host: str, port: int, *, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _blender_live_line() -> str:
    """Fast readiness for Blender without a full MCP round-trip."""
    host, port = "localhost", 9876
    try:
        from frontend.ui_web.plugin_host_api import prefs_plugin_get

        prefs = prefs_plugin_get("blender") or {}
        host = str(prefs.get("host") or host).strip() or host
        port = int(str(prefs.get("port") or port) or port)
    except Exception:
        pass
    if _tcp_open(host, port):
        return (
            f"**READY** — socket {host}:{port} is up. `blender_*` works now "
            "(no UEFN listener needed). For mesh/modeling requests where the user "
            "did not already say Blender or UEFN, ask once: Blender or UEFN? "
            "Then use that path."
        )
    return (
        f"**NOT READY** — nothing on {host}:{port}. "
        "Teach: Edit → Preferences → Add-ons → enable **Blender MCP**, then "
        "N → BlenderMCP → Connect (or restart Blender). "
        "Do **not** blame UEFN / the Fortnite listener."
    )


def _live_status_for(plugin_id: str) -> str:
    if plugin_id == "blender":
        return _blender_live_line()
    return (
        "**ENABLED** — tools registered on the app MCP. "
        "Use them without waiting for the UEFN listener unless the tool itself is editor-only."
    )


def enabled_desktop_plugins_prompt_block() -> str:
    """Inject into agent system prompt so duckies see MCP/plugin truth."""
    try:
        from backend.uefn_plugins.host import uefn_agent_tool_rows
    except Exception:
        return ""

    try:
        rows: list[dict[str, Any]] = list(uefn_agent_tool_rows() or [])
    except Exception:
        return ""
    if not rows:
        return ""

    lines = [
        "## Enabled Store desktop plugins (live MCP status)",
        "These extend the **uefn-ducky** app MCP (Settings → Skills & MCP). "
        "They are **not** nested MCP servers and usually do **not** need the UEFN editor.",
        "When status is READY/ENABLED, those tools work without waiting for UEFN — "
        "never stall on \"UEFN MCP reconnecting\". For modeling, ask Blender vs UEFN "
        "once if the user did not already choose.",
    ]
    for row in rows:
        pid = str(row.get("id") or "").strip()
        if not pid:
            continue
        label = str(row.get("label") or pid)
        tools = [str(t) for t in (row.get("tool_names") or []) if str(t).strip()]
        n = len(tools)
        sample = ", ".join(tools[:6])
        if n > 6:
            sample += ", …"
        status = _live_status_for(pid)
        lines.append(f"- **{label}** (`{pid}`, {n} tools{f': {sample}' if sample else ''})")
        lines.append(f"  - {status}")
        if pid == "blender":
            lines.append(
                "  - Skill pack **blender** (plugin-owned): `skill_read_subskill(\"blender\", \"connection\")` "
                "when teaching connect; after the user picks Blender, model with "
                "`blender_execute_blender_code` (and peers)."
            )
    return "\n".join(lines) + "\n"
