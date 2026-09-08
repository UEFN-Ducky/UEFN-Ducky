"""Editor changes and blocked attempts in the change journal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.workspace import editor_record as rec
from backend.workspace import events, identity, runtime
from backend.workspace.identity import RunContext
from backend.workspace.journal import OP_EDITOR, FileChangeJournal
from backend.workspace.test_schemas import validate
from backend.workspace.writer import ProjectWriter

SIDECAR = {
    "v": 1,
    "command": "set_actor_transform",
    "kind": "actor",
    "facet": "transform",
    "targets": [{"kind": "actor", "id": "8F2A", "guid": "8F2A", "label": "VerifyCube",
                 "path": "/Game/Map.Map:PersistentLevel.Cube"}],
    "before": {"location": [0.0, 0.0, 0.0]},
    "inverse": [{"command": "set_actor_transform",
                 "params": {"actor_path": "/Game/Map.Map:PersistentLevel.Cube", "location": [0, 0, 0]}}],
    "created": [],
    "revertable": "auto",
    "reason": "",
    "summary": "moved +250 on Z",
    "outcome": "ok",
}


@pytest.fixture
def env(tmp_path: Path):
    root = tmp_path / "Proj"
    (root / "Content" / "Verse").mkdir(parents=True)
    storage = tmp_path / "store"
    journal = FileChangeJournal(lambda _r: storage)
    writer = ProjectWriter.for_root(str(root), journal=journal)
    runtime.reset_for_tests(writer)
    rec.reset_for_tests()
    events.reset_for_tests()
    rec.add_observer(rec.JournalEditorObserver())
    yield root, storage, journal, writer
    runtime.reset_for_tests(None)
    rec.reset_for_tests()
    events.reset_for_tests()


def as_run(run_id="r1", **kw):
    return identity.bind(RunContext(run_id=run_id, conv_id=kw.pop("conv_id", "c1"),
                                    ducky_name=kw.pop("ducky", "Hacker"), **kw))


def body(sidecar=None):
    out = {"success": True, "result": {}}
    if sidecar is not None:
        out["_ducky"] = sidecar
    return out


# --- recording --------------------------------------------------------------------


def test_an_editor_change_lands_in_the_same_run_as_file_writes(env) -> None:
    root, _storage, journal, writer = env
    token = as_run()
    try:
        writer.write_text("Content/Verse/a.verse", "one\n", tool="workspace_write_file")
        rec.record("set_actor_transform", {"actor_path": "/Game/Map.Map:PersistentLevel.Cube"},
                   body(SIDECAR), ok=True)
    finally:
        identity.reset(token)

    run = journal.get_run("r1", project_root=str(root))
    assert validate("changeset_run", run) == []
    assert [e["op"] for e in run["entries"]] == ["write", OP_EDITOR]
    editor = run["entries"][1]
    assert editor["path"] == "uefn://actor/8F2A/transform"
    assert editor["outcome"] == "ok"
    assert editor["in_lane"] is None and editor["conflict"] is None
    assert editor["editor"]["revertable"] == "auto"
    assert editor["editor"]["targets"][0]["label"] == "VerifyCube"
    assert editor["editor"]["inverse"][0]["command"] == "set_actor_transform"


def test_the_before_and_after_states_are_stored_and_readable(env) -> None:
    root, _storage, journal, _writer = env
    token = as_run()
    try:
        rec.record("set_actor_transform", {"actor_path": "/x"}, body(SIDECAR), ok=True)
    finally:
        identity.reset(token)
    entry = journal.get_run("r1", project_root=str(root))["entries"][0]
    assert entry["before_blob"] and entry["after_blob"]
    contents = journal.entry_contents("r1", entry["seq"], project_root=str(root))
    assert json.loads(contents["before"]) == {"location": [0.0, 0.0, 0.0]}
    assert json.loads(contents["after"])["params"] == {"actor_path": "/x"}


def test_repeated_edits_to_one_target_share_a_slot(env) -> None:
    root, _storage, journal, _writer = env
    token = as_run()
    try:
        for _ in range(3):
            rec.record("set_actor_transform", {"actor_path": "/x"}, body(SIDECAR), ok=True)
    finally:
        identity.reset(token)
    entries = journal.get_run("r1", project_root=str(root))["entries"]
    assert len({e["path"] for e in entries}) == 1


def test_an_editor_slot_never_enters_the_file_index(env) -> None:
    root, storage, _journal, writer = env
    token = as_run()
    try:
        writer.write_text("Content/Verse/a.verse", "one\n")
        rec.record("set_actor_transform", {"actor_path": "/x"}, body(SIDECAR), ok=True)
    finally:
        identity.reset(token)
    index = json.loads((storage / "index.json").read_text(encoding="utf-8"))
    assert list(index) == ["Content/Verse/a.verse"]


def test_nothing_is_recorded_outside_a_run(env, monkeypatch) -> None:
    root, storage, _journal, _writer = env
    for key in RunContext().to_env():
        monkeypatch.delenv(key, raising=False)
    rec.record("set_actor_transform", {"actor_path": "/x"}, body(SIDECAR), ok=True)
    assert not list((storage / "runs").glob("*.json")) if (storage / "runs").exists() else True


def test_an_editor_op_event_reaches_the_ui(env) -> None:
    seen: list[dict] = []
    events.register_sink(seen.append)
    token = as_run()
    try:
        rec.record("set_actor_transform", {"actor_path": "/x"}, body(SIDECAR), ok=True)
    finally:
        identity.reset(token)
    [event] = [e for e in seen if e.get("type") == "editor_op"]
    assert event["conv_id"] == "c1"  # without this every open panel refreshes
    assert event["target_label"] == "VerifyCube"
    assert event["summary"] == "moved +250 on Z"
    assert event["revertable"] == "auto"


# --- blocked and failed attempts ---------------------------------------------------


def test_a_refused_command_is_recorded_but_changes_nothing(env) -> None:
    root, storage, journal, _writer = env
    token = as_run()
    try:
        rec.record("delete_actors", {"actor_paths": ["/x"]}, {},
                   ok=False, error="Refused: never delete island actors.")
    finally:
        identity.reset(token)
    run = journal.get_run("r1", project_root=str(root))
    assert validate("changeset_run", run) == []
    [entry] = run["entries"]
    assert entry["outcome"] == "blocked"
    assert entry["after_blob"] is None and entry["after_hash"] == ""
    assert "Refused" in entry["reason"]
    # Nothing landed, so nothing is indexed and nothing is revertable.
    index_file = storage / "index.json"
    assert not index_file.exists() or json.loads(index_file.read_text(encoding="utf-8")) == {}


def test_blocked_entries_are_skipped_by_revert(env) -> None:
    root, _storage, journal, writer = env
    token = as_run()
    try:
        writer.write_text("Content/Verse/a.verse", "v1\n")
        rec.record("delete_actors", {}, {}, ok=False, error="Refused: never delete island actors.")
    finally:
        identity.reset(token)
    result = journal.revert_run("r1", project_root=str(root))
    assert result["reverted"] == [1]  # the file write only
    assert journal.get_run("r1", project_root=str(root))["entries"][1]["reverted"] is False


def test_a_failed_command_is_recorded_as_failed(env) -> None:
    root, _storage, journal, _writer = env
    token = as_run()
    try:
        rec.record("set_actor_transform", {"actor_path": "/gone"}, {},
                   ok=False, error="Actor not found: /gone")
    finally:
        identity.reset(token)
    [entry] = journal.get_run("r1", project_root=str(root))["entries"]
    assert entry["outcome"] == "failed"
    assert entry["editor"]["revertable"] == "none"


# --- export ------------------------------------------------------------------------


def test_export_keeps_files_and_editor_changes_apart(env) -> None:
    root, _storage, journal, writer = env
    token = as_run()
    try:
        writer.write_text("Content/Verse/a.verse", "one\n")
        rec.record("set_actor_transform", {"actor_path": "/x"}, body(SIDECAR), ok=True)
        rec.record("delete_actors", {}, {}, ok=False, error="Refused: never delete island actors.")
    finally:
        identity.reset(token)
    export = journal.export_run("r1", project_root=str(root))
    assert validate("changeset_export", export) == []
    # Editor slots are never files, in either direction.
    assert [f["path"] for f in export["files"]] == ["Content/Verse/a.verse"]
    assert all(not f["path"].startswith("uefn://") for f in export["files"])
    assert all(e["slot"].startswith("uefn://") for e in export["editor"])
    # The refused delete is an editor operation too — listed, marked blocked.
    by_slot = {e["slot"]: e for e in export["editor"]}
    assert by_slot["uefn://actor/8F2A/transform"]["revertable"] == "auto"
    assert [e["outcome"] for e in export["editor"]].count("blocked") == 1


def test_the_tool_summary_counts_files_editor_and_blocked_separately(env, monkeypatch) -> None:
    root, _storage, journal, writer = env
    from backend.tools.core import changesets as tools

    monkeypatch.setattr(tools, "workspace_roots", lambda: [str(root)])
    monkeypatch.setattr(tools, "tool_json", lambda payload, pretty=False: json.dumps(payload))
    token = as_run()
    try:
        writer.write_text("Content/Verse/a.verse", "one\n")
        rec.record("set_actor_transform", {"actor_path": "/x"}, body(SIDECAR), ok=True)
        rec.record("delete_actors", {}, {}, ok=False, error="Refused: never delete island actors.")
    finally:
        identity.reset(token)
    [summary] = json.loads(tools.changeset_list())["runs"]
    assert summary["files"] == ["Content/Verse/a.verse"]
    assert summary["editor_changes"] == 1
    assert summary["editor_manual"] == 0
    assert summary["blocked"] == 1
