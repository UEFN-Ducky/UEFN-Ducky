"""MCP tools the embedded agent must never call directly."""

from __future__ import annotations

# Escape hatches and low-level duplicates — agent uses focused wrappers or thin serial tools.
EXCLUDED_TOOLS = frozenset(
    {
        "shutdown",
        "ai_scene_command",
        "ai_generate_python",
        "listener_command",
        # Removed bulk/batch (must not appear in tool list)
        "batch_commands",
        "bulk_wire_verse_device",
        "bulk_set_verse_editables",
        "bulk_set_device_settings",
        "setup_verse_device",
        "spawn_actor_batch",
        "set_verse_fields",
        # Low-level read duplicates
        "get_verse_editables",
        "get_device_settings",
        "list_creative_devices",
        # Prefer set_creative_device_fields wrapper
        "set_device_settings",
        "wire_player_spawners",
    }
)
