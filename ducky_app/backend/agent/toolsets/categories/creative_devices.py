"""Native Creative devices — item granters, triggers, spawners."""

from __future__ import annotations

CORE_TOOLS = frozenset()

EXTENDED_TOOLS = frozenset(
    {
        "find_devices",
        "inspect_creative_device",
        "set_creative_device_fields",
    }
)

TOOLS = CORE_TOOLS | EXTENDED_TOOLS

PLAN_TOOLS = frozenset(
    {
        "find_devices",
        "inspect_creative_device",
    }
)
