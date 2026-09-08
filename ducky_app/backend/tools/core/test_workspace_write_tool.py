"""workspace_write_file / workspace_read_file go through ProjectWriter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.tools.core import system
from backend.workspace import runtime
from backend.workspace.writer import ProjectWriter


class RecordingJournal:
    def __init__(self) -> None:
        self.records: list = []
        self.reads: list[tuple[str, str]] = []

    def record(self, record):
        self.records.append(record)
        return {"run_id": "r", "seq": len(self.records)}

    def note_read(self, path: str, content_hash: str, project_root: str = "") -> None:
        self.reads.append((path, content_hash))


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    root = tmp_path / "Proj"
    (root / "Content" / "Verse").mkdir(parents=True)
    journal = RecordingJournal()
    runtime.reset_for_tests(ProjectWriter.for_root(str(root), journal=journal))
    monkeypatch.setattr(system, "resolve_workspace_path", lambda rel: str(root / rel))
    yield root, journal
    runtime.reset_for_tests(None)


def _payload(text: str) -> dict:
    # tool_json may emit TOON; the tests only need the JSON form.
    return json.loads(text)


def test_write_tool_uses_pipeline_and_reports_changeset(project, monkeypatch) -> None:
    root, journal = project
    monkeypatch.setattr(system, "tool_json", lambda payload, pretty=False: json.dumps(payload))
    out = _payload(system.workspace_write_file("Content/Verse/a.verse", "one\ntwo\n"))
    assert (root / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "one\ntwo\n"
    assert out["relative_path"] == "Content/Verse/a.verse"
    assert out["lines_added"] == 2 and out["before_content"] == ""
    assert out["changeset"] == {"run_id": "r", "seq": 1}
    assert Path(out["path"]).is_absolute()
    assert len(journal.records) == 1 and journal.records[0].tool == "workspace_write_file"


def test_write_tool_refuses_guarded_paths(project, monkeypatch) -> None:
    root, _ = project
    monkeypatch.setattr(system, "tool_json", lambda payload, pretty=False: json.dumps(payload))
    with pytest.raises(ValueError):
        system.workspace_write_file("Content/Verse/Fortnite.digest.verse", "x")
    with pytest.raises(ValueError):
        system.workspace_write_file("Saved/x.txt", "x")
    assert not (root / "Saved").exists()


def test_read_tool_notes_the_base_for_conflicts(project, monkeypatch) -> None:
    root, journal = project
    (root / "Content" / "Verse" / "a.verse").write_text("base\n", encoding="utf-8")
    monkeypatch.setattr(system, "tool_json", lambda payload, pretty=False: json.dumps(payload))
    monkeypatch.setattr("frontend.ui_web.verse_editor.agent_sync.emit_for_bridge_tool", lambda *a, **k: None)
    out = _payload(system.workspace_read_file("Content/Verse/a.verse"))
    assert out["content"] == "base\n"
    from backend.workspace.paths import content_hash

    assert journal.reads == [("Content/Verse/a.verse", content_hash("base\n"))]
