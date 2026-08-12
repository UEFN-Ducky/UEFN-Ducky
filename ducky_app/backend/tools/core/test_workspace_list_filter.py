"""Workspace list_dir skips heavy/binary UEFN dirs."""

from __future__ import annotations

from backend.tools.core.system import _workspace_entry_allowed


def test_skips_saved_intermediate_uasset():
    assert _workspace_entry_allowed("Saved") is False
    assert _workspace_entry_allowed("Intermediate") is False
    assert _workspace_entry_allowed("DerivedDataCache") is False
    assert _workspace_entry_allowed("Foo.uasset") is False
    assert _workspace_entry_allowed("Map.umap") is False


def test_allows_verse_and_text():
    assert _workspace_entry_allowed("Verse") is True
    assert _workspace_entry_allowed("Content") is True
    assert _workspace_entry_allowed("foo.verse") is True
    assert _workspace_entry_allowed("readme.md") is True
