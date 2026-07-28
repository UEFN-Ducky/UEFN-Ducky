"""Isolated Verse editor module for UEFN Ducky panel."""

from frontend.ui_web.verse_editor.api import VerseEditorApi
from frontend.ui_web.verse_editor.agent_sync import emit_editor_events

__all__ = ["VerseEditorApi", "emit_editor_events"]
