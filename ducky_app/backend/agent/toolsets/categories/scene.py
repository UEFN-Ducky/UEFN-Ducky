"""Level actors — placement, transforms, and mesh properties."""

from __future__ import annotations

CORE_TOOLS = frozenset()

EXTENDED_TOOLS = frozenset(
    {
        "get_all_actors",
        "get_selected_actors",
        "get_actor_properties",
        "set_actor_properties",
        "spawn_actor",
        "set_actor_transform",
        "select_actors",
        "focus_selected",
        "delete_actors",
    }
)

TOOLS = EXTENDED_TOOLS

PLAN_TOOLS = frozenset(
    {
        "get_all_actors",
        "get_selected_actors",
        "get_actor_properties",
    }
)
