"""Per-project memory — named entries shared by the project's duckies.

Skills-style: the active project's index rides in every prompt; entry bodies and
capture tools are deferred via ducky_call_tool (Cursor-style tool loading).
"""

from __future__ import annotations

CORE_TOOLS = frozenset()

EXTENDED_TOOLS = frozenset(
    {
        "project_memory_list",
        "project_memory_get",
        "project_memory_save",
        "project_memory_append",
        "ducky_memory_overview",
        "project_memory_delete",
    }
)

TOOLS = CORE_TOOLS | EXTENDED_TOOLS

PLAN_TOOLS = frozenset(
    {
        "project_memory_list",
        "project_memory_get",
        "ducky_memory_overview",
    }
)
