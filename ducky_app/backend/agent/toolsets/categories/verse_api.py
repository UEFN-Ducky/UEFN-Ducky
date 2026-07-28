"""Verse digests — offline API ground truth (Fortnite / Verse / UnrealEngine / Assets)."""

from __future__ import annotations

CORE_TOOLS = frozenset()

EXTENDED_TOOLS = frozenset(
    {
        "search_verse_digest",
        "get_verse_api",
        "list_verse_digests",
        "list_verse_modules",
        "list_verse_types",
        "list_verse_devices",
    }
)

TOOLS = CORE_TOOLS | EXTENDED_TOOLS

PLAN_TOOLS = frozenset(
    {
        "search_verse_digest",
        "get_verse_api",
        "list_verse_digests",
        "list_verse_modules",
        "list_verse_types",
        "list_verse_devices",
    }
)
