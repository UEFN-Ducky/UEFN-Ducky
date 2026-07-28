"""Feature flag for Verse editor."""

from __future__ import annotations

from frontend.settings import PanelSettings


def verse_editor_enabled() -> bool:
    s = PanelSettings.load()
    return bool(getattr(s, "verse_editor_enabled", True))
