"""Agent tool categories — one module per domain."""

from __future__ import annotations

from backend.agent.toolsets.categories import (
    assets,
    creative_devices,
    memory,
    panel,
    project,
    scene,
    testing,
    verse_api,
    verse_devices,
    workspace,
)

# Discord tools ship with the Discord UEFN plugin (agent.tools contribution).
_CATEGORIES = (
    panel,
    project,
    workspace,
    memory,
    verse_api,
    verse_devices,
    creative_devices,
    scene,
    assets,
    testing,
)

# Tiny always-hot floor for tools[]; everything else via ducky_call_tool.
CORE_TOOLS: frozenset[str] = frozenset().union(*(c.CORE_TOOLS for c in _CATEGORIES))

EXTENDED_TOOLS: frozenset[str] = frozenset().union(*(c.EXTENDED_TOOLS for c in _CATEGORIES))

TOOLS: frozenset[str] = CORE_TOOLS | EXTENDED_TOOLS

PLAN_TOOLS: frozenset[str] = frozenset().union(*(c.PLAN_TOOLS for c in _CATEGORIES))
