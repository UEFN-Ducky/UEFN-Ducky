"""Health, level save, skill, and listener tools."""

from __future__ import annotations

CORE_TOOLS = frozenset()

EXTENDED_TOOLS = frozenset(
    {
        "ping",
        "reload_listener",
        "save_current_level",
        "get_project_info",
        "uefn_skill",
        "get_level_info",
        "get_viewport_camera",
        "set_viewport_camera",
    }
)

TOOLS = CORE_TOOLS | EXTENDED_TOOLS

PLAN_TOOLS = frozenset(
    {
        "ping",
        "reload_listener",
        "get_project_info",
        "get_level_info",
        "uefn_skill",
    }
)
