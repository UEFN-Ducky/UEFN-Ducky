"""Agent writes must land in file history with source=agent."""

from __future__ import annotations

from pathlib import Path

import frontend.ui_web.verse_editor.file_history as file_history


def test_record_agent_write_keeps_restore_point_and_ai_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(file_history, "default_app_data_dir", lambda: tmp_path)
    project_root = str(tmp_path / "MyProject")
    rel = "Content/Verse/Game.verse"
    before = "using { /Fortnite.com/Devices }\n"
    after = "using { /Fortnite.com/Devices }\n# AI edit\n"

    file_history.record_agent_write(rel, before, after, project_root=project_root)

    entries = file_history.list_entries(rel, project_root=project_root)
    assert len(entries) == 2
    newest, previous = entries[0], entries[1]
    assert newest["source"] == "agent"
    assert previous["source"] == ""

    agent_body = file_history.read_entry(rel, newest["id"], project_root=project_root)
    assert agent_body["content"] == after
    prior_body = file_history.read_entry(rel, previous["id"], project_root=project_root)
    assert prior_body["content"] == before
