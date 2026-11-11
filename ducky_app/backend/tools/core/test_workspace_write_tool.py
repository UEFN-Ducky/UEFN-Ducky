"""workspace_write_file / workspace_read_file go through ProjectWriter."""

from __future__ import annotations

import json
import os
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
    out = _payload(system.workspace_write_file("Content/Verse/a.verse", content="one\ntwo\n"))
    assert (root / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "one\ntwo\n"
    assert out["relative_path"] == "Content/Verse/a.verse"
    assert out["lines_added"] == 2
    assert out["changeset"] == {"run_id": "r", "seq": 1}
    assert len(journal.records) == 1 and journal.records[0].tool == "workspace_write_file"


def test_write_tool_result_is_small_and_never_echoes_the_old_file(project) -> None:
    root, _ = project
    old = "x = 1\n" * 2000
    (root / "Content" / "a.txt").write_text(old, encoding="utf-8")
    text = system.workspace_write_file("Content/a.txt", content="y\n" * 450)
    out = _payload(text)
    assert "before_content" not in out and "x = 1" not in text
    assert "path" not in out  # relative_path only, no absolute twin
    assert all(v not in (None, "", [], {}) for v in out.values())
    assert out["bytes_written"] == 900 and out["lines_added"] == 450
    assert len(text.encode("utf-8")) < 400


def test_write_tool_writes_anywhere_in_the_project(project, monkeypatch) -> None:
    root, _ = project
    monkeypatch.setattr(system, "tool_json", lambda payload, pretty=False, **_k: json.dumps(payload))
    system.workspace_write_file("Content/Verse/Fortnite.digest.verse", content="x")
    system.workspace_write_file("Saved/x.txt", content="x")
    assert (root / "Saved" / "x.txt").read_text(encoding="utf-8") == "x"
    with pytest.raises(ValueError):
        system.workspace_write_file("../escape.txt", content="x")


def test_read_tool_notes_the_base_for_conflicts(project, monkeypatch) -> None:
    root, journal = project
    (root / "Content" / "Verse" / "a.verse").write_text("base\n", encoding="utf-8")
    monkeypatch.setattr(system, "tool_json", lambda payload, pretty=False: json.dumps(payload))
    monkeypatch.setattr("frontend.ui_web.verse_editor.agent_sync.emit_for_bridge_tool", lambda *a, **k: None)
    out = _payload(system.workspace_read_file("Content/Verse/a.verse", format="json"))
    assert out["content"] == "base\n"
    from backend.workspace.paths import content_hash

    assert journal.reads == [("Content/Verse/a.verse", content_hash("base\n"))]


def test_read_tool_returns_plain_text_like_read_files(project, monkeypatch) -> None:
    root, _ = project
    (root / "Content" / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    monkeypatch.setattr("frontend.ui_web.verse_editor.agent_sync.emit_for_bridge_tool", lambda *a, **k: None)
    text = system.workspace_read_file("Content/a.txt")
    assert text == "Content/a.txt  lines 1-3 of 3\none\ntwo\nthree\n"
    part = system.workspace_read_file("Content/a.txt", start_line=2, end_line=2, line_numbers=True)
    head, body = part.split("\n", 1)
    assert head == "Content/a.txt  lines 2-2 of 3"
    assert "2" in body and "two" in body and "one" not in body


def test_read_and_write_tools_say_what_they_are_for_first() -> None:
    for fn, words in ((system.workspace_read_file, "Read one text file"),
                      (system.workspace_write_file, "Create or replace a whole text file")):
        first = (fn.__doc__ or "").strip().splitlines()[0]
        assert first.startswith(words) and "never" in first and len(first) < 120


def test_write_tool_takes_path_like_the_read_tool(project, monkeypatch) -> None:
    root, _ = project
    monkeypatch.setattr(system, "tool_json", lambda payload, pretty=False, **_: json.dumps(payload))
    _payload(system.workspace_write_file(path="Content/Verse/b.verse", content="x\n"))
    assert (root / "Content" / "Verse" / "b.verse").read_text(encoding="utf-8") == "x\n"


def test_read_tool_picks_one_file_from_both_path_spellings() -> None:
    # Oct 17 2026: relative_path="." with an absolute path read the project folder itself
    # ("Not a file: ...\Content"), and the model kept retrying.
    absolute = str(Path("C:/Trailers/run/bible.json"))
    assert system._file_arg(".", absolute) == absolute
    assert system._file_arg("", "bible.json") == "bible.json"
    assert system._file_arg("Verse/ui.verse", "") == "Verse/ui.verse"
    assert system._file_arg("Content/run", absolute) == absolute
    assert system._file_arg("run/01_refs/bible.json", "bible.json") == "run/01_refs/bible.json"
    assert system._file_arg("run/01_refs", "review.md") == os.path.join("run/01_refs", "review.md")


def test_write_tools_take_path_when_relative_path_is_dot() -> None:
    # Oct 17 2026: a Ducky AI lead's Verse write (relative_path=".", path=<file>) was refused.
    from backend.tools.core.workspace_code import path_arg

    assert path_arg(".", "Content/DuckyAITest/treasure_hunt_device.verse") == "Content/DuckyAITest/treasure_hunt_device.verse"
    with pytest.raises(ValueError, match="different files"):
        path_arg("Content/a.verse", "Content/b.verse")


def test_write_tools_join_a_folder_and_a_file_name() -> None:
    # Oct 17 2026: a Ducky AI chat split the folder and the file across the two arguments
    # five ways and every write was refused.
    from backend.tools.core.workspace_code import path_arg

    want = "Content/DuckyAITest/treasure_hunt_device.verse"
    assert path_arg("Content/DuckyAITest", "treasure_hunt_device.verse") == want
    assert path_arg("Content/DuckyAITest/", want) == want
    assert path_arg("Content/DuckyAITest", want) == want
    assert path_arg("DuckyAITest/treasure_hunt_device.verse", "Content") == want
    assert path_arg(want, "treasure_hunt_device.verse") == want
    assert path_arg("Content\\DuckyAITest", "treasure_hunt_device.verse") == want
    with pytest.raises(ValueError, match="different files"):
        path_arg("Content/A", "Content/B")
    with pytest.raises(ValueError, match="different files"):
        path_arg("Content", "C:/elsewhere/x.verse")
