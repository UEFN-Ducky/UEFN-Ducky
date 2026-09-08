"""FileChangeJournal: per-run ledger, conflicts, revert round trips, export, retention."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from backend.workspace import events, identity, runtime
from backend.workspace.identity import RunContext
from backend.workspace.journal import FileChangeJournal
from backend.workspace.test_schemas import validate
from backend.workspace.writer import ProjectWriter


@pytest.fixture
def env(tmp_path: Path):
    root = tmp_path / "Proj"
    (root / "Content" / "Verse").mkdir(parents=True)
    storage = tmp_path / "appdata" / "changesets" / "proj"
    clock = {"now": 1_000_000.0}
    journal = FileChangeJournal(lambda _root: storage, clock=lambda: clock["now"])
    writer = ProjectWriter.for_root(str(root), journal=journal, clock=lambda: clock["now"])
    runtime.reset_for_tests(writer)
    events.reset_for_tests()
    yield root, storage, journal, writer, clock
    runtime.reset_for_tests(None)
    events.reset_for_tests()


def as_run(run_id: str, **kw):
    return identity.bind(RunContext(run_id=run_id, conv_id=f"conv-{run_id}", ducky_name=kw.pop("ducky", run_id.title()), **kw))


def test_run_document_is_created_lazily_and_validates(env) -> None:
    root, storage, journal, writer, _ = env
    assert not list((storage / "runs").glob("*.json")) if storage.exists() else True
    token = as_run("r1", model="m", group_id="g")
    try:
        res = writer.write_text("Content/Verse/a.verse", "one\n", tool="workspace_write_file")
        res2 = writer.write_text("Content/Verse/a.verse", "two\n", tool="workspace_write_file")
    finally:
        identity.reset(token)
    assert res.changeset == {"run_id": "r1", "seq": 1, "in_lane": None, "conflict": None}
    assert res2.changeset["seq"] == 2 and res2.warning == ""
    run = journal.get_run("r1", project_root=str(root))
    assert validate("changeset_run", run) == []
    assert run["status"] == "running" and run["ducky_name"] == "R1" and run["group_id"] == "g"
    e1, e2 = run["entries"]
    assert e1["op"] == "write" and e1["before_blob"] is None and e1["after_blob"]
    assert e2["before_blob"] == e1["after_blob"]
    assert journal.entry_contents("r1", 2, project_root=str(root)) == {
        "path": "Content/Verse/a.verse", "before": "one\n", "after": "two\n"
    }


def test_user_writes_touch_only_the_index(env, monkeypatch) -> None:
    root, storage, journal, writer, _ = env
    for key in RunContext().to_env():
        monkeypatch.delenv(key, raising=False)
    writer.write_text("Content/Verse/a.verse", "user\n", tool="panel_save")
    assert list((storage / "runs").glob("*.json")) == []
    index = json.loads((storage / "index.json").read_text(encoding="utf-8"))
    assert index["Content/Verse/a.verse"]["run_id"] == "" and index["Content/Verse/a.verse"]["source"] == "user"


def test_end_run_closes_and_prunes(env) -> None:
    root, storage, journal, writer, clock = env
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/a.verse", "x\n")
    finally:
        identity.reset(token)
    journal.end_run("r1", "done", project_root=str(root))
    run = journal.get_run("r1", project_root=str(root))
    assert run["status"] == "done" and run["ended"] == clock["now"]
    journal.end_run("missing", "done", project_root=str(root))  # no doc: no error


def test_stale_base_conflict_is_flagged_not_denied(env) -> None:
    root, storage, journal, writer, _ = env
    seen: list[dict] = []
    events.register_sink(seen.append)
    (root / "Content" / "Verse" / "a.verse").write_text("base\n", encoding="utf-8")
    hacker = as_run("hacker", ducky="Hacker")
    try:
        writer.note_read("Content/Verse/a.verse", "base\n")
    finally:
        identity.reset(hacker)
    solo = as_run("solo", ducky="Solo")
    try:
        writer.write_text("Content/Verse/a.verse", "solo edit\n")
    finally:
        identity.reset(solo)
    hacker = as_run("hacker", ducky="Hacker")
    try:
        res = writer.write_text("Content/Verse/a.verse", "hacker edit\n")
    finally:
        identity.reset(hacker)
    assert (root / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "hacker edit\n"
    assert res.changeset["conflict"]["kind"] == "stale_base"
    assert res.changeset["conflict"]["other_ducky"] == "Solo"
    assert "changed by Solo" in res.warning
    assert [e["kind"] for e in seen] == ["conflict"]
    run = journal.get_run("hacker", project_root=str(root))
    assert run["entries"][0]["conflict"]["kind"] == "stale_base"


def test_concurrent_writer_conflict_only_while_other_run_is_open(env) -> None:
    root, storage, journal, writer, _ = env
    a = as_run("a")
    try:
        writer.write_text("Content/Verse/shared.verse", "a1\n")
    finally:
        identity.reset(a)
    b = as_run("b")
    try:
        res = writer.write_text("Content/Verse/shared.verse", "b1\n")
    finally:
        identity.reset(b)
    assert res.changeset["conflict"]["kind"] == "concurrent_writer"
    journal.end_run("b", "done", project_root=str(root))
    journal.end_run("a", "done", project_root=str(root))
    c = as_run("c")
    try:
        res2 = writer.write_text("Content/Verse/shared.verse", "c1\n")
    finally:
        identity.reset(c)
    assert res2.changeset["conflict"] is None


def test_revert_run_restores_files_newest_first(env) -> None:
    root, storage, journal, writer, _ = env
    seen: list[dict] = []
    events.register_sink(seen.append)
    (root / "Content" / "Verse" / "a.verse").write_text("orig\n", encoding="utf-8")
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/a.verse", "v1\n")
        writer.write_text("Content/Verse/a.verse", "v2\n")
        writer.create("Content/Verse/new.verse", "new\n")
    finally:
        identity.reset(token)
    result = journal.revert_run("r1", project_root=str(root))
    assert result["ok"] and result["reverted"] == [1, 2, 3] and result["skipped_modified"] == []
    assert (root / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "orig\n"
    assert not (root / "Content" / "Verse" / "new.verse").exists()
    run = journal.get_run("r1", project_root=str(root))
    assert run["status"] == "reverted" and all(e["reverted"] for e in run["entries"])
    assert all(e["reverted_by_run"].startswith("revert:") for e in run["entries"])
    revert_runs = [r for r in journal.list_runs(project_root=str(root)) if r["run_id"].startswith("revert:")]
    assert len(revert_runs) == 1 and revert_runs[0]["source"] == "revert"
    assert seen[-1]["type"] == "files_reverted" and set(seen[-1]["paths"]) == {"Content/Verse/a.verse", "Content/Verse/new.verse"}


def test_revert_skips_files_modified_since_unless_forced(env) -> None:
    root, storage, journal, writer, _ = env
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/a.verse", "v1\n")
    finally:
        identity.reset(token)
    (root / "Content" / "Verse" / "a.verse").write_text("someone else\n", encoding="utf-8")
    result = journal.revert_run("r1", project_root=str(root))
    assert result["reverted"] == [] and result["skipped_modified"] == [{"seq": 1, "path": "Content/Verse/a.verse"}]
    assert journal.get_run("r1", project_root=str(root))["status"] == "running"
    forced = journal.revert_run("r1", project_root=str(root), force=True)
    assert forced["reverted"] == [1]
    assert not (root / "Content" / "Verse" / "a.verse").exists()  # created by r1, so revert removes it


def test_revert_entry_of_delete_and_move(env) -> None:
    root, storage, journal, writer, _ = env
    verse = root / "Content" / "Verse"
    (verse / "gone.verse").write_text("bye\n", encoding="utf-8")
    (verse / "old.verse").write_text("mv\n", encoding="utf-8")
    token = as_run("r1")
    try:
        writer.path_op("delete", "Content/Verse/gone.verse", perform=(verse / "gone.verse").unlink)

        def _move() -> None:
            (verse / "Sub").mkdir(exist_ok=True)
            os.replace(verse / "old.verse", verse / "Sub" / "new.verse")

        writer.path_op("move", "Content/Verse/Sub/new.verse", source="Content/Verse/old.verse", perform=_move)
    finally:
        identity.reset(token)
    r = journal.revert_entry("r1", 1, project_root=str(root))
    assert r["reverted"] == [1] and (verse / "gone.verse").read_text(encoding="utf-8") == "bye\n"
    r2 = journal.revert_entry("r1", 2, project_root=str(root))
    assert r2["reverted"] == [2] and (verse / "old.verse").is_file() and not (verse / "Sub" / "new.verse").exists()
    assert journal.get_run("r1", project_root=str(root))["status"] == "reverted"
    again = journal.revert_entry("r1", 2, project_root=str(root))
    assert again["reverted"] == [] and "already reverted" in again["errors"][0]


def test_revert_entry_reverts_every_write_to_that_file(env) -> None:
    root, storage, journal, writer, _ = env
    (root / "Content" / "Verse" / "a.verse").write_text("orig\n", encoding="utf-8")
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/a.verse", "v1\n")
        writer.write_text("Content/Verse/a.verse", "v2\n")
        writer.write_text("Content/Verse/b.verse", "other\n")
    finally:
        identity.reset(token)
    result = journal.revert_entry("r1", 2, project_root=str(root))
    assert result["reverted"] == [1, 2]
    assert (root / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "orig\n"
    assert (root / "Content" / "Verse" / "b.verse").is_file()
    assert journal.get_run("r1", project_root=str(root))["status"] == "partially_reverted"


def test_export_matches_schema(env) -> None:
    root, storage, journal, writer, _ = env
    token = as_run("r1", profile_id="hacker", model="anthropic:x")
    try:
        writer.write_text("Content/Verse/a.verse", "v1\n")
    finally:
        identity.reset(token)
    export = journal.export_run("r1", project_root=str(root))
    assert validate("changeset_export", export) == []
    assert export["author"] == {"id": "hacker", "name": "R1", "model": "anthropic:x"}
    assert export["files"][0]["path"] == "Content/Verse/a.verse" and export["files"][0]["op"] == "write"


def test_list_runs_filters_and_orders(env) -> None:
    root, storage, journal, writer, clock = env
    for rid, conv_kw in (("r1", {"group_id": "g1"}), ("r2", {"group_id": "g2"})):
        clock["now"] += 10
        token = as_run(rid, **conv_kw)
        try:
            writer.write_text(f"Content/Verse/{rid}.verse", "x\n")
        finally:
            identity.reset(token)
    runs = journal.list_runs(project_root=str(root))
    assert [r["run_id"] for r in runs] == ["r2", "r1"]
    assert [r["run_id"] for r in journal.list_runs(project_root=str(root), group_id="g1")] == ["r1"]
    assert [r["run_id"] for r in journal.list_runs(project_root=str(root), conv_id="conv-r2")] == ["r2"]


def test_prune_drops_old_closed_runs_and_orphan_blobs(env) -> None:
    root, storage, journal, writer, clock = env
    token = as_run("old")
    try:
        writer.write_text("Content/Verse/a.verse", "old\n")
    finally:
        identity.reset(token)
    journal.end_run("old", "done", project_root=str(root))
    clock["now"] += 40 * 86400
    token = as_run("fresh")
    try:
        writer.write_text("Content/Verse/b.verse", "fresh\n")
    finally:
        identity.reset(token)
    stats = journal.prune(project_root=str(root))
    assert stats == {"removed_runs": 1, "removed_blobs": 1}
    assert [r["run_id"] for r in journal.list_runs(project_root=str(root))] == ["fresh"]


def test_disabled_journal_records_nothing(tmp_path: Path) -> None:
    root = tmp_path / "Proj"
    (root / "Content").mkdir(parents=True)
    journal = FileChangeJournal(lambda _r: tmp_path / "store", enabled=lambda: False)
    writer = ProjectWriter.for_root(str(root), journal=journal)
    token = as_run("r1")
    try:
        res = writer.write_text("Content/a.txt", "x")
    finally:
        identity.reset(token)
    assert res.changeset == {} and not (tmp_path / "store").exists()


def test_revert_survives_history_pruning(env, monkeypatch) -> None:
    """The journal's blobs are the revert source; editor history retention is irrelevant."""
    root, storage, journal, writer, _ = env
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/a.verse", "v0\n")
        for i in range(1, 130):
            writer.write_text("Content/Verse/a.verse", f"v{i}\n")
    finally:
        identity.reset(token)
    result = journal.revert_run("r1", project_root=str(root))
    assert result["ok"] and len(result["reverted"]) == 130
    assert not (root / "Content" / "Verse" / "a.verse").exists()
