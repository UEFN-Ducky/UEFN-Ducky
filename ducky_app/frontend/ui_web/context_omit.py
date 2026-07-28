"""Shared context-omit helpers (no imports from context_tokens/context_control)."""

from __future__ import annotations

from frontend.chat_store import Conversation

VALID_SEGMENT_IDS = frozenset(
    {
        "all",
        "conversation",
        "summarized",
        "system",
        "personality",
        "mcp_tools",
        "tools",
        "rules",
        "skill",
        "mcp",
        "tool_index",
        "draft",
    }
)
CONTEXT_OMIT_IDS = frozenset(
    {"system", "personality", "tools", "rules", "skill", "mcp", "tool_index"}
)


def mcp_tools_omitted(omit: frozenset[str]) -> bool:
    return "mcp_tools" in omit or "tools" in omit or "mcp" in omit


def tool_index_omitted(omit: frozenset[str]) -> bool:
    return "tool_index" in omit or mcp_tools_omitted(omit)


def context_omit_set(conv: Conversation) -> frozenset[str]:
    raw = conv.context_omit or []
    stored = frozenset(
        str(x) for x in raw if x in CONTEXT_OMIT_IDS or x == "mcp_tools"
    )
    omit: set[str] = {x for x in stored if x not in {"tools", "mcp"}}
    if mcp_tools_omitted(stored):
        omit.add("mcp_tools")
    if "tool_index" in stored or mcp_tools_omitted(stored):
        omit.add("tool_index")
    return frozenset(omit)


def omitted_for_ui(omit: frozenset[str]) -> list[str]:
    return sorted(omit)
