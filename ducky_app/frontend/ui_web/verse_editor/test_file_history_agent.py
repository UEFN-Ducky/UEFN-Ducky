"""File history records who wrote each version (schema v2) and stays readable for v1 entries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import frontend.ui_web.verse_editor.file_history as file_history

REL = "Content/Verse/Game.verse"
BEFORE = "using { /Fortnite.com/Devices }\n"
AFTER = "using { /Fortnite.com/Devices }\n# AI edit\n"
WRITER = {
    "source": "agent",
    "run_id": "run-1",
    "conv_id": "conv-1",
    "profile_id": "hacker",
    "ducky_name": "Hacker",
    "model": "anthropic:claude-sonnet-5",
    "group_id": "hub-1",
    "coding_agent": "ducky",
    "tool": "workspace_write_file",
}


@pytest.fixture
def root(tmp_path: Path, monkeypatch) -> str:
    monkeypatch.setattr(file_history, "default_app_data_dir", lambda: tmp_path)
    return str(tmp_path / "MyProject")


def test_record_write_keeps_restore_point_and_attributed_version(root: str) -> None:
    ids = file_history.record_write(REL, BEFORE, AFTER, project_root=root, writer=WRITER)
    assert ids["before_id"] and ids["after_id"] and ids["before_id"] != ids["after_id"]

    entries = file_history.list_entries(REL, project_root=root)
    assert len(entries) == 2
    newest, previous = entries
    assert newest["id"] == ids["after_id"]
    assert newest["schema_version"] == 2
    assert newest["source"] == "agent"
    assert newest["ducky_name"] == "Hacker" and newest["run_id"] == "run-1"
    assert newest["model"] == "anthropic:claude-sonnet-5" and newest["tool"] == "workspace_write_file"
    assert previous["source"] == "" and previous["ducky_name"] == ""  # restore point is unattributed
    assert file_history.read_entry(REL, newest["id"], project_root=root)["content"] == AFTER
    assert file_history.read_entry(REL, previous["id"], project_root=root)["content"] == BEFORE


def test_user_save_records_only_the_restore_point(root: str) -> None:
    ids = file_history.record_write(REL, BEFORE, AFTER, project_root=root, writer={"source": "user"})
    assert ids["before_id"] and ids["after_id"] is None
    entries = file_history.list_entries(REL, project_root=root)
    assert [e["source"] for e in entries] == [""]


def test_new_file_has_no_restore_point(root: str) -> None:
    ids = file_history.record_write(REL, None, AFTER, project_root=root, writer=WRITER)
    assert ids["before_id"] is None and ids["after_id"]


def test_entry_on_disk_matches_schema_and_omits_empty_attribution(root: str) -> None:
    from backend.workspace.test_schemas import validate

    file_history.record_write(REL, None, AFTER, project_root=root, writer={"source": "agent", "run_id": "r"})
    entry_file = next((Path(root).parent / "file_history").rglob("*.json"))
    data = json.loads(entry_file.read_text(encoding="utf-8"))
    assert validate("file_history_entry", data) == []
    assert data["run_id"] == "r" and "ducky_name" not in data


def test_v1_entry_reads_back_with_empty_attribution(root: str) -> None:
    entries_dir = file_history._entries_dir(REL, root)  # noqa: SLF001
    legacy = {
        "id": "1700000000000",
        "path": REL,
        "saved_at": 1700000000,
        "bytes": len(BEFORE),
        "preview": BEFORE.strip(),
        "content_hash": file_history._content_hash(BEFORE),  # noqa: SLF001
        "content": BEFORE,
    }
    (entries_dir / "1700000000000.json").write_text(json.dumps(legacy), encoding="utf-8")
    [entry] = file_history.list_entries(REL, project_root=root)
    assert entry["schema_version"] == 1 and entry["source"] == "" and entry["run_id"] == ""


def test_record_agent_write_is_a_deprecated_shim(root: str) -> None:
    with pytest.warns(DeprecationWarning):
        file_history.record_agent_write(REL, BEFORE, AFTER, project_root=root)
    newest = file_history.list_entries(REL, project_root=root)[0]
    assert newest["source"] == "agent" and newest["run_id"] == ""
