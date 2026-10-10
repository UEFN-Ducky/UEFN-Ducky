"""Codex native edits use the same edit-card metadata as workspace writes."""

import json

import pytest

from frontend.ui_web.verse_editor import agent_sync


@pytest.mark.parametrize("tool_name", ["file_change", "apply_patch", "ApplyPatch", "mcp__uefn__apply_patch"])
def test_codex_edit_result_produces_edit_card(tool_name, monkeypatch):
    monkeypatch.setattr(agent_sync.io, "get_cached", lambda _path: None)

    def unexpected_read(_path):
        pytest.fail("Explicit before/after records must not need disk or history")

    monkeypatch.setattr(agent_sync.io, "read_file", unexpected_read)
    monkeypatch.setattr(agent_sync, "_before_from_history", unexpected_read)
    assert agent_sync.is_write_tool(tool_name)
    meta = agent_sync.file_edit_meta_for_stream(
        tool_name,
        {"path": "src/example.py"},
        json.dumps({"path": "src/example.py", "before_content": "old\n", "content": "new\n"}),
    )
    assert meta == {
        "path": "src/example.py",
        "before": "old\n",
        "after": "new\n",
        "linesAdded": 1,
        "linesRemoved": 1,
        "kind": "write",
    }


def test_command_execution_is_not_misclassified_as_an_edit():
    assert not agent_sync.is_write_tool("command_execution")
    assert agent_sync.file_edit_meta_for_stream("command_execution", {}, "ok") is None
