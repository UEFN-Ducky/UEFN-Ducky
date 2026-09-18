"""Isolated Verse editor module for UEFN Ducky panel.

Heavy LSP/API imports are lazy so ``import backend.tools`` (MCP bridge) does
not load verse-lsp just to register ``workspace_list_verse_errors``.
"""

from __future__ import annotations

__all__ = ["VerseEditorApi", "emit_editor_events"]


def __getattr__(name: str):
    if name == "VerseEditorApi":
        from frontend.ui_web.verse_editor.api import VerseEditorApi

        return VerseEditorApi
    if name == "emit_editor_events":
        from frontend.ui_web.verse_editor.agent_sync import emit_editor_events

        return emit_editor_events
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
