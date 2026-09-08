"""changeset_* MCP tools over a temp project and journal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.tools.core import changesets as tools
from backend.workspace import identity, runtime
from backend.workspace.identity import RunContext
from backend.workspace.journal import FileChangeJournal
from backend.workspace.writer import ProjectWriter


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    root = tmp_path / "Proj"
    (root / "Content" / "Verse").mkdir(parents=True)
    journal = FileChangeJournal(lambda _r: tmp_path / "store")
    writer = ProjectWriter.for_root(str(root), journal=journal)
    runtime.reset_for_tests(writer)
    monkeypatch.setattr(tools, "workspace_roots", lambda: [str(root)])
    monkeypatch.setattr(tools, "tool_json", lambda payload, pretty=False: json.dumps(payload))
    token = identity.bind(RunContext(run_id="r1", conv_id="hacker", ducky_name="Hacker", group_id="hub"))
    try:
        writer.write_text("Content/Verse/a.verse", "v1\n", tool="workspace_write_file")
    finally:
        identity.reset(token)
    yield root, journal
    runtime.reset_for_tests(None)


def test_list_and_export(env) -> None:
    out = json.loads(tools.changeset_list(group_id="hub"))
    assert out["ok"] and [r["run_id"] for r in out["runs"]] == ["r1"]
    assert out["runs"][0]["files"] == ["Content/Verse/a.verse"] and out["runs"][0]["ducky_name"] == "Hacker"
    export = json.loads(tools.changeset_export("r1"))
    assert export["format"] == "ducky.changeset/1" and export["author"]["name"] == "Hacker"


def test_revert_by_owner_leader_and_user(env) -> None:
    root, _ = env
    stranger = identity.bind(RunContext(run_id="x", conv_id="artist", group_id="hub"))
    try:
        with pytest.raises(ValueError, match="group leader"):
            tools.changeset_revert("r1")
    finally:
        identity.reset(stranger)
    leader = identity.bind(RunContext(run_id="y", conv_id="architect", group_id="hub", leader_conv_id="architect", is_leader=True))
    try:
        result = json.loads(tools.changeset_revert("r1"))
    finally:
        identity.reset(leader)
    assert result["ok"] and result["reverted"] == [1]
    assert not (root / "Content" / "Verse" / "a.verse").exists()


def test_revert_reports_skipped_and_force_hint(env) -> None:
    root, _ = env
    (root / "Content" / "Verse" / "a.verse").write_text("edited later\n", encoding="utf-8")
    result = json.loads(tools.changeset_revert("r1"))  # user context (nothing bound)
    assert result["reverted"] == [] and result["skipped_modified"] and "force=true" in result["next"]


def test_journal_disabled_gives_clear_error(env) -> None:
    from backend.workspace.journal import NullJournal

    runtime.get_writer().set_journal(NullJournal())
    with pytest.raises(ValueError, match="disabled"):
        tools.changeset_list()
