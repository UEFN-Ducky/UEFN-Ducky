"""Epic verse-lsp detection and bridge.

Submodules (``diagnostics_cache``, ``project_root``) must import without
starting the language-server process or loading ``bridge``.
"""

from __future__ import annotations

__all__ = ["LspBridge", "detect_verse_lsp"]


def __getattr__(name: str):
    if name == "LspBridge":
        from frontend.ui_web.verse_editor.lsp.bridge import LspBridge

        return LspBridge
    if name == "detect_verse_lsp":
        from frontend.ui_web.verse_editor.lsp.detect import detect_verse_lsp

        return detect_verse_lsp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
