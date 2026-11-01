"""Epic answers an unknown toolset/tool with text, not an error: the pool must raise on it."""

from __future__ import annotations

from backend.mcp_plugins import epic_verse_files
from backend.mcp_plugins.client_pool import epic_not_found
from backend.tools.tester import session_play


def test_not_found_replies_are_failures() -> None:
    assert epic_not_found("Toolset 'ValkyrieToolset.SessionToolset' not found")
    assert epic_not_found("Error: Tool 'StartGame' not found in toolset X")


def test_normal_replies_pass() -> None:
    assert not epic_not_found('{"returnValue": []}')
    assert not epic_not_found("Session started")
    assert not epic_not_found("")


def test_callers_use_43_00_toolset_names() -> None:
    # 43.00 renamed these; the old ValkyrieToolset.* names answer "not found".
    assert session_play._SESSION == "ValkyrieFortniteToolset.SessionToolset"
    assert session_play._DEVICE == "ValkyrieFortniteToolset.DeviceToolset"
    assert epic_verse_files.VERSE_TOOLSET == "VerseToolset.VerseToolset"
