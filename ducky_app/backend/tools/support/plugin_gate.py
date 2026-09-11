"""Gate MCP tool bodies on Store desktop-plugin enable + chat allowlist."""

from __future__ import annotations

import functools
from typing import Any, Callable

# Disk / skill tools the agent must keep even if the owning Store plugin is off.
# Verse create/edit is host-disk — never wait on Epic MCP or a Store toggle.
HOST_DISK_TOOLS = frozenset(
    {
        "workspace_list_verse_errors",
        "workspace_open_verse_file",
        "workspace_compile_verse",
        "workspace_push_verse_changes",
        "skill_read_subskill",
        "uefn_skill",
        "search_verse_digest",
        "get_verse_api",
        "list_verse_digests",
        "list_verse_types",
        "list_verse_devices",
        "list_verse_modules",
    }
)

# Installed Store plugins that must come back on when an agent needs them.
# Partial settings imports used to leave verse off → Unknown tool / "enable in Store".
_CORE_STORE_HEAL = frozenset({"verse", "uefn"})


def heal_enable_store_plugin(plugin_id: str) -> bool:
    """Turn on an installed Store/bundled core plugin. Never asks the user to click Store."""
    from backend.uefn_plugins.host import is_plugin_enabled
    from backend.uefn_plugins.store import load_plugin_manifest, set_uefn_plugin_enabled

    pid = (plugin_id or "").strip().lower()
    if pid not in _CORE_STORE_HEAL:
        return False
    if is_plugin_enabled(pid):
        return True
    manifest = load_plugin_manifest(pid)
    if not manifest:
        return False
    source = str(manifest.get("source") or "store")
    if source not in ("store", "bundled"):
        return False
    try:
        result = set_uefn_plugin_enabled(pid, True)
    except Exception:
        return False
    return bool(result.get("ok")) and is_plugin_enabled(pid)


def require_plugin(plugin_id: str) -> None:
    from backend.uefn_plugins.host import is_plugin_enabled, uefn_agent_tools_allowed

    pid = (plugin_id or "").strip().lower()
    if not pid:
        raise ValueError("plugin gate missing plugin id")
    if not is_plugin_enabled(pid):
        if not heal_enable_store_plugin(pid):
            raise ValueError(f"Plugin '{pid}' is not available")
    if not uefn_agent_tools_allowed(pid):
        raise ValueError(f"Plugin '{pid}' tools are off for this chat")


def plugin_mcp_tool(plugin_id: str, **tool_kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Like ``@mcp.tool`` but refuses calls when the Store plugin is off."""
    from backend.server import mcp

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            require_plugin(plugin_id)
            return fn(*args, **kwargs)

        return mcp.tool(**tool_kwargs)(wrapper)

    return deco