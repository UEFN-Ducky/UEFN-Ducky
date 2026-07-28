"""Modular tool selection for the UEFN-Ducky embedded agent."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.agent.toolsets.categories import CORE_TOOLS, PLAN_TOOLS
from backend.agent.toolsets.destructive import DESTRUCTIVE_TOOLS
from backend.agent.toolsets.excluded import EXCLUDED_TOOLS
from backend.agent.toolsets.intents import INTENT_KEYWORDS
from backend.agent.toolsets.mcp_plugins import plugin_destructive_tool_names
from backend.agent.toolsets.plan_safe import is_plan_safe_tool

if TYPE_CHECKING:
    from mcp.types import Tool

__all__ = [
    "CORE_TOOLS",
    "PLAN_TOOLS",
    "DESTRUCTIVE_TOOLS",
    "EXCLUDED_TOOLS",
    "INTENT_KEYWORDS",
    "is_destructive",
    "is_plan_safe_tool",
    "select_tools",
    "selection_context_text",
    "tools_used_in_history",
    "effective_tool_name",
]


def is_destructive(tool_name: str) -> bool:
    if tool_name in DESTRUCTIVE_TOOLS:
        return True
    if tool_name in plugin_destructive_tool_names():
        return True
    try:
        from backend.uefn_plugins.host import plugin_destructive_desktop_tools

        return tool_name in plugin_destructive_desktop_tools()
    except Exception:
        return False


def effective_tool_name(name: str, arguments: dict[str, Any] | None = None) -> str:
    """Inner tool for ducky_call_tool; otherwise the outer name."""
    n = (name or "").strip()
    if n != "ducky_call_tool":
        return n
    args = arguments if isinstance(arguments, dict) else {}
    inner = str(args.get("name") or "").strip()
    return inner or n


def tools_used_in_history(history: list[dict[str, Any]] | None) -> set[str]:
    used: set[str] = set()
    for message in history or []:
        for block in message.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            name = str(block.get("name") or "").strip()
            if not name:
                continue
            args = block.get("arguments") if isinstance(block.get("arguments"), dict) else {}
            used.add(effective_tool_name(name, args))
    return used


def selection_context_text(
    user_message: str = "",
    *,
    history: list[dict[str, Any]] | None = None,
    max_messages: int = 8,
) -> str:
    """Build text for intent matching from the latest and recent USER turns only.

    Assistant replies must not feed intent matching — a capability answer that
    mentions "material/asset/spawn" would unlock every bundle for the whole chat.
    Tool continuity is handled separately by tools_used_in_history.
    """
    parts: list[str] = []
    if user_message.strip():
        parts.append(user_message.strip())
    tail = [m for m in (history or []) if m.get("role") == "user"][-max_messages:]
    for message in tail:
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            parts.append(content.strip())
    return "\n".join(parts)


def select_tools(
    all_tools: list[Tool],
    user_message: str = "",
    *,
    plan_only: bool = False,
    listener_online: bool = False,
    history: list[dict[str, Any]] | None = None,
    conv: Any = None,
) -> list[Tool]:
    """Return the always-hot floor for tools[].

    Cursor-style: only CORE (or PLAN floor) schemas go in tools[]. Extended /
    plugin / listener tools are invoked via ducky_call_tool after ducky_get_tools.
    ``listener_online``, intent keywords, and history unlocks no longer inflate
    tools[] — kept as unused kwargs for call-site compatibility.
    """
    del user_message, listener_online, history, conv  # deferred via ducky_call_tool
    by_name = {t.name: t for t in all_tools if t.name not in EXCLUDED_TOOLS}
    core = PLAN_TOOLS if plan_only else CORE_TOOLS
    selected: dict[str, Tool] = {}
    for name in core:
        if name in by_name:
            selected[name] = by_name[name]
    return list(selected.values())
