"""Verse script devices (VerseDevice_C) — @editable fields in the level."""

from __future__ import annotations

CORE_TOOLS = frozenset()

EXTENDED_TOOLS = frozenset(
    {
        "inspect_verse_device",
        "wire_verse_device_ref",
        "set_verse_editable",
        "set_currency_config_entries",
        "resize_verse_array",
        "patch_verse_array_entry",
        "wire_verse_device_array",
        "wire_verse_prop_assets",
        "set_verse_texture_icon",
        "list_verse_reference_types",
        "list_verse_property_hashes",
        "spawn_actor",
        "set_actor_label",
        "save_current_level",
    }
)

TOOLS = CORE_TOOLS | EXTENDED_TOOLS

PLAN_TOOLS = frozenset(
    {
        "inspect_verse_device",
        "list_verse_reference_types",
        "list_verse_property_hashes",
    }
)
