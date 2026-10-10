"""Recorded authorship is historical evidence, not current-byte ownership."""

import json
from pathlib import Path

import pytest

from backend.tools.core import changesets
from backend.workspace import identity, runtime
from backend.workspace.identity import RunContext
from backend.workspace.journal import FileChangeJournal
from backend.workspace.writer import ProjectWriter, content_hash


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    roots = [tmp_path / name for name in ("first", "second")]
    for root in roots:
        (root / "Content").mkdir(parents=True)
    journal = FileChangeJournal(lambda root: tmp_path / "ledger" / Path(root).name)
    writers = [ProjectWriter.for_root(str(root), journal=journal) for root in roots]
    runtime.reset_for_tests(writers[0])
    monkeypatch.setattr(changesets, "workspace_roots", lambda: [str(roots[0])])
    monkeypatch.setattr(changesets, "tool_json", lambda payload, pretty=False: json.dumps(payload))
    yield roots, journal, writers
    runtime.reset_for_tests(None)


def write(writer, run, body, **fields):
    token = identity.bind(RunContext(run_id=run, **fields))
    try:
        return writer.write_text("Content/file.txt", body, tool="workspace_write_file")
    finally:
        identity.reset(token)


@pytest.mark.parametrize("reader", ["list", "get", "export", "contents", "index"])
def test_external_bytes_do_not_change_historical_attribution(ledger, reader):
    roots, journal, writers = ledger
    root = roots[0]
    write(writers[0], "a", "first\n", conv_id="alice", ducky_name="Alice", group_id="g")
    (root / "Content/file.txt").write_bytes(b"external between records\n")
    write(writers[0], "b", "second\n", conv_id="bob", ducky_name="Bob", group_id="g")
    reads = {
        "list": lambda: json.loads(changesets.changeset_list(group_id="g")),
        "get": lambda: journal.get_run("b", project_root=str(root)),
        "export": lambda: json.loads(changesets.changeset_export("b")),
        "contents": lambda: json.loads(changesets.changeset_contents("b", 1)),
        "index": lambda: journal.index_stamp("Content/file.txt", project_root=str(root)),
    }
    recorded = reads[reader]()
    unknown = b"external current bytes\n"
    (root / "Content/file.txt").write_bytes(unknown)
    assert reads[reader]() == recorded
    assert (root / "Content/file.txt").read_bytes() == unknown
    exported = json.loads(changesets.changeset_export("b"))
    assert exported["author"]["name"] == "Bob"
    assert exported["files"][0]["after_hash"] == content_hash("second\n")
    assert exported["files"][0]["after_hash"] != content_hash(unknown.decode())
    body = json.loads(changesets.changeset_contents("b", 1))
    assert body["before"] == "external between records\n"
    assert body["after"] == "second\n"
    assert json.loads(changesets.changeset_export("a"))["author"]["name"] == "Alice"


def test_project_run_and_conversation_scope_are_not_lane_claims(ledger, monkeypatch):
    roots, journal, writers = ledger
    first = write(writers[0], "same-run", "project 0\n", conv_id="chat-0",
                  group_id="group-0", lane=("Content/file.txt", "Content/never-written.txt"))
    assert first.changeset
    before = journal.get_run("same-run", project_root=str(roots[0]))
    rejected = write(writers[1], "same-run", "project 1\n", conv_id="chat-1", group_id="group-1")
    assert "applied but not ledgered" in rejected.warning
    assert not rejected.changeset
    # Journal refusal is after the filesystem mutation, not a writer rollback.
    assert (roots[1] / "Content/file.txt").read_text() == "project 1\n"
    assert journal.get_run("same-run", project_root=str(roots[0])) == before
    assert journal.list_runs(project_root=str(roots[1])) == []
    with pytest.raises(ValueError, match="not found"):
        journal.get_run("same-run", project_root=str(roots[1]))
    i, root = 0, roots[0]
    runtime.reset_for_tests(writers[i])
    monkeypatch.setattr(changesets, "workspace_roots", lambda root=root: [str(root)])
    rows = json.loads(changesets.changeset_list(conv_id=f"chat-{i}", group_id=f"group-{i}"))["runs"]
    assert len(rows) == 1 and rows[0]["conv_id"] == f"chat-{i}"
    assert rows[0]["lane"] == {"write_allowed": ["Content/file.txt", "Content/never-written.txt"]}
    assert rows[0]["files"] == ["Content/file.txt"]
    assert json.loads(changesets.changeset_list(conv_id=f"chat-{1-i}"))["runs"] == []
    assert json.loads(changesets.changeset_contents("same-run", 1))["after"] == f"project {i}\n"
    assert not (root / "Content/never-written.txt").exists()
    with pytest.raises(ValueError, match="not found"):
        journal.get_run("absent", project_root=str(root))


def test_missing_identity_and_freshness_are_not_inferred(ledger):
    roots, journal, writers = ledger
    write(writers[0], "known-run", "recorded\n")
    export = json.loads(changesets.changeset_export("known-run"))
    assert export["author"] == {"id": "", "name": "", "model": ""}
    assert export["lane"] is None and export["run"]["ended"] is None
    assert "task_id" not in export["run"] and "freshness" not in export
    write(writers[0], "", "unattributed\n")
    rows = journal.list_runs(project_root=str(roots[0]))
    assert [r["run_id"] for r in rows] == ["known-run"]
    stamp = journal.index_stamp("Content/file.txt", project_root=str(roots[0]))
    assert stamp["run_id"] == stamp["conv_id"] == stamp["ducky_name"] == ""
    assert json.loads(changesets.changeset_contents("known-run", 1))["after"] == "recorded\n"
