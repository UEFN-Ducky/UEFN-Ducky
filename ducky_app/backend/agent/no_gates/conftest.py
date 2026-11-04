from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_island_watch_thread(monkeypatch):
    """A working UEFN call starts the island watch; these tests never want a real thread."""
    from frontend.ui_web.verse_editor.urc import island_watch

    monkeypatch.setattr(island_watch, "on_uefn_call_ok", lambda: None)
