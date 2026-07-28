"""Built-in tool groups exposed as toggleable pseudo-plugins.

Only ``builtin_ducky`` remains as a Settings toggle (``ducky_*`` / ``workspace_*``).

Thin UEFN bridge tools (ping, listener, logs, …) stay always registered on the
shared FastMCP and are never stripped by builtin-group filtering. Domain editor
tools live in Store desktop plugins nested under UEFN-Ducky.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.types import Tool

BUILTIN_DUCKY = "builtin_ducky"
# Kept for migration / older chat JSON — no longer a real group.
BUILTIN_UEFN = "builtin_uefn"

BUILTIN_GROUP_IDS: tuple[str, ...] = (BUILTIN_DUCKY,)

_GROUP_META: dict[str, dict[str, str]] = {
    BUILTIN_DUCKY: {
        "label": "Ducky app tools",
        "description": "The UEFN-Ducky app's own tools: offline Verse file editing & error checking, plus status, chats, terminals.",
    },
}

# Always-on listener / bridge infra — not a toggleable "UEFN tools" group.
BRIDGE_TOOLS: frozenset[str] = frozenset(
    {
        "ping",
        "listener_command",
        "reload_listener",
        "execute_python",
        "get_log",
        "get_editor_log",
        "shutdown",
        "uefn_editor_python_hints",
    }
)

_DUCKY_TOOL_PREFIXES = ("ducky_", "workspace_")


def is_builtin_group(plugin_id: str) -> bool:
    return plugin_id in BUILTIN_GROUP_IDS


def is_bridge_tool(tool_name: str) -> bool:
    return tool_name in BRIDGE_TOOLS


def builtin_group_for_tool(tool_name: str) -> str | None:
    """Return builtin group id, or None for bridge / plugin-owned tools."""
    if tool_name in BRIDGE_TOOLS:
        return None
    if tool_name.startswith(_DUCKY_TOOL_PREFIXES):
        return BUILTIN_DUCKY
    # Legacy: non-ducky core tools used to be builtin_uefn. They are plugin-owned
    # once extracted; until then treat as bridge-adjacent (never strip here —
    # plugin filter / NEVER_EXPOSE owns the cutover).
    return None


def get_enabled_builtin_group_ids() -> list[str]:
    """Globally enabled groups: all groups minus the persisted disabled list."""
    from frontend.settings import PanelSettings

    settings = PanelSettings.load()
    disabled = {
        g
        for g in (getattr(settings, "disabled_builtin_toolsets", None) or [])
        if is_builtin_group(g)
    }
    return [gid for gid in BUILTIN_GROUP_IDS if gid not in disabled]


_ACTIVE_BUILTIN_GROUPS: ContextVar[tuple[str, ...] | None] = ContextVar(
    "uefn_ducky_active_builtin_toolsets", default=None
)


def set_active_builtin_groups(group_ids: list[str] | None) -> None:
    """Scope built-in tool groups to the current run thread/task tree."""
    if group_ids is None:
        _ACTIVE_BUILTIN_GROUPS.set(None)
        return
    ids = tuple(gid for gid in group_ids if is_builtin_group(str(gid)))
    _ACTIVE_BUILTIN_GROUPS.set(ids)


def effective_builtin_group_ids() -> list[str]:
    """Per-run override if set, else globally enabled group ids."""
    active = _ACTIVE_BUILTIN_GROUPS.get()
    if active is not None:
        return list(active)
    return get_enabled_builtin_group_ids()


def filter_builtin_tools(tools: list[Tool]) -> list[Tool]:
    """Drop Ducky-app tools when builtin_ducky is disabled; never strip bridge tools."""
    enabled = set(effective_builtin_group_ids())
    if BUILTIN_DUCKY in enabled:
        return list(tools)
    out: list[Tool] = []
    for t in tools:
        name = getattr(t, "name", None) or ""
        if name in BRIDGE_TOOLS:
            out.append(t)
            continue
        if name.startswith(_DUCKY_TOOL_PREFIXES):
            continue
        out.append(t)
    return out


def set_builtin_group_enabled(group_id: str, enabled: bool) -> dict[str, Any]:
    """Persist the global default for one group (stored as a disabled list)."""
    from frontend.settings import PanelSettings, replace

    if group_id == BUILTIN_UEFN:
        # Removed group — clear from disabled list and no-op.
        settings = PanelSettings.load()
        disabled = [
            g
            for g in (getattr(settings, "disabled_builtin_toolsets", None) or [])
            if g != BUILTIN_UEFN and is_builtin_group(g)
        ]
        settings = replace(settings, disabled_builtin_toolsets=disabled)
        settings.save()
        return {
            "ok": True,
            "plugin_id": group_id,
            "enabled": True,
            "disabled_builtin_toolsets": disabled,
            "removed": True,
        }
    if not is_builtin_group(group_id):
        raise ValueError(f"Not a built-in tool group: {group_id!r}")
    settings = PanelSettings.load()
    disabled = [g for g in (getattr(settings, "disabled_builtin_toolsets", None) or []) if is_builtin_group(g)]
    if enabled:
        disabled = [g for g in disabled if g != group_id]
    elif group_id not in disabled:
        disabled.append(group_id)
    settings = replace(settings, disabled_builtin_toolsets=disabled)
    settings.save()
    return {
        "ok": True,
        "plugin_id": group_id,
        "enabled": enabled,
        "disabled_builtin_toolsets": disabled,
    }


def builtin_group_rows() -> list[dict[str, Any]]:
    """Pseudo-plugin rows shaped like backend.mcp_plugins.store.list_mcp_plugins()."""
    enabled = set(get_enabled_builtin_group_ids())
    rows: list[dict[str, Any]] = []
    for gid in BUILTIN_GROUP_IDS:
        meta = _GROUP_META[gid]
        rows.append(
            {
                "id": gid,
                "label": meta["label"],
                "description": meta["description"],
                "version": 1,
                "kind": "builtin",
                "transport": "builtin",
                "tags": [],
                "enabled": gid in enabled,
                "default_enabled": True,
                "requirements_note": "",
                "setup_steps": [],
                "tool_prefix": "",
                "intents": [],
                "path": "",
            }
        )
    return rows


def count_builtin_group_tools(group_id: str) -> int:
    """Tool count for one group (used by the panel's Test button)."""
    import asyncio

    from backend.server import mcp

    if not is_builtin_group(group_id):
        return 0
    tools = asyncio.run(mcp.list_tools())
    return sum(1 for t in tools if builtin_group_for_tool(t.name) == group_id)


def migrate_drop_builtin_uefn(toolset_ids: list[str] | None) -> list[str] | None:
    """Strip legacy builtin_uefn from a chat/settings toolset list."""
    if toolset_ids is None:
        return None
    return [x for x in toolset_ids if str(x).strip() != BUILTIN_UEFN]
