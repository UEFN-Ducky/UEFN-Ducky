"""Phase 3 (ledger, blobs, file history, watch index): parity, import, GC."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.store.repos import ledger as ledger_repo
from backend.workspace import identity, runtime
from backend.workspace.identity import RunContext
from backend.workspace.journal import FileChangeJournal
from backend.workspace.writer import ProjectWriter


@pytest.fixture(params=["db", "files"])
def backend(request, monkeypatch) -> str:
    monkeypatch.setenv("DUCKY_STORE_BACKEND", request.param)
    return request.param


@pytest.fixture
def island(tmp_path: Path) -> Path:
    root = tmp_path / "Island"
    (root / "Content" / "Verse").mkdir(parents=True)
    (root / "Island.uefnproject").write_text("{}")
    return root


@pytest.fixture
def journal(tmp_path: Path):
    j = FileChangeJournal(lambda _root: tmp_path / "ledger" / "Island_abc")
    yield j


def _write(writer: ProjectWriter, path: str, text: str, run_id: str, conv_id: str = "c1") -> None:
    token = identity.bind(RunContext(run_id=run_id, conv_id=conv_id, ducky_name="Coder"))
    try:
        writer.write_text(path, text)
    finally:
        identity.reset(token)


def test_record_list_contents_and_revert(backend: str, island: Path, journal: FileChangeJournal) -> None:
    writer = ProjectWriter.for_root(str(island), journal=journal)
    runtime.reset_for_tests(writer)
    try:
        _write(writer, "Content/Verse/a.verse", "v1", "r1")
        _write(writer, "Content/Verse/a.verse", "v2", "r1")
        journal.end_run("r1", "done", project_root=str(island))
        runs = journal.list_runs(project_root=str(island))
        assert [r["run_id"] for r in runs] == ["r1"]
        run = journal.get_run("r1", project_root=str(island))
        assert [e["seq"] for e in run["entries"]] == [1, 2]
        contents = journal.entry_contents("r1", 2, project_root=str(island))
        assert contents["before"] == "v1" and contents["after"] == "v2"
        assert journal.index_stamp("Content/Verse/a.verse", project_root=str(island))["run_id"] == "r1"
        result = journal.revert_run("r1", project_root=str(island))
        assert result.get("ok") is True or result.get("reverted")
        assert not (island / "Content" / "Verse" / "a.verse").exists()
    finally:
        runtime.reset_for_tests(None)


def test_archive_delete_prune(backend: str, island: Path, journal: FileChangeJournal) -> None:
    writer = ProjectWriter.for_root(str(island), journal=journal)
    runtime.reset_for_tests(writer)
    try:
        for i in range(3):
            _write(writer, f"Content/Verse/f{i}.verse", f"body {i}", f"run{i}", conv_id=f"c{i}")
            journal.end_run(f"run{i}", "done", project_root=str(island))
        assert journal.set_archived(project_root=str(island), run_id="run0")["updated"] == 1
        assert [r["run_id"] for r in journal.list_runs(project_root=str(island), archived=True)] == ["run0"]
        assert journal.delete_runs(project_root=str(island), run_id="run1")["removed_runs"] == 1
        remaining = {r["run_id"] for r in journal.list_runs(project_root=str(island), archived=None)}
        assert remaining == {"run0", "run2"}
        pruned = journal.prune(project_root=str(island), keep_runs=0, max_age_days=0)
        assert pruned["removed_runs"] == 1  # run2 goes, run0 is archived and stays
        assert {r["run_id"] for r in journal.list_runs(project_root=str(island), archived=None)} == {"run0"}
    finally:
        runtime.reset_for_tests(None)


def test_blob_sweep_keeps_referenced_rows(island: Path, journal: FileChangeJournal) -> None:
    writer = ProjectWriter.for_root(str(island), journal=journal)
    runtime.reset_for_tests(writer)
    try:
        _write(writer, "Content/Verse/k.verse", "keep me", "rk")
        journal.end_run("rk", "done", project_root=str(island))
        orphan = ledger_repo.blob_put("nobody references this")
        assert ledger_repo.blob_exists(orphan)
        removed = ledger_repo.sweep_blobs()
        assert removed >= 1 and not ledger_repo.blob_exists(orphan)
        run = journal.get_run("rk", project_root=str(island))
        assert ledger_repo.blob_get(run["entries"][0]["after_blob"]) == "keep me"
    finally:
        runtime.reset_for_tests(None)


def test_file_history_parity(backend: str, island: Path) -> None:
    from frontend.ui_web.verse_editor import file_history as fh

    root = str(island)
    rec = fh.record_write("Content/Verse/h.verse", "old", "new", root, writer={"source": "agent", "run_id": "r9", "ducky_name": "Coder"})
    assert rec["before_id"] and rec["after_id"]
    again = fh.record_write("Content/Verse/h.verse", "new", "new", root, writer={"source": "agent"})
    assert again["after_id"] == rec["after_id"]  # identical content: no new version
    rows = fh.list_entries("Content/Verse/h.verse", root)
    assert [r["id"] for r in rows] == [rec["after_id"], rec["before_id"]]
    assert rows[0]["ducky_name"] == "Coder" and rows[0]["source"] == "agent"
    assert "content" not in rows[0]
    assert fh.read_entry("Content/Verse/h.verse", rec["before_id"], root)["content"] == "old"
    with pytest.raises(ValueError):
        fh.read_entry("Content/Verse/h.verse", "nope", root)


def test_file_history_caps_at_100_rows(island: Path) -> None:
    from backend.store.repos import history

    for i in range(105):
        history.put("Island_x", "Content/a.verse", f"{1000 + i}", f"body {i}", preview="", saved_at=i, attribution={})
    assert len(history.list_rows("Island_x", "Content/a.verse")) == 100
    assert history.content("Island_x", "Content/a.verse", "1000") is None


def test_watch_index_rows(island: Path, journal: FileChangeJournal) -> None:
    from backend.workspace import human_watch as hw

    storage = journal._storage(str(island))
    hw._save_saved(storage, {"Content/Verse/w.verse": {"hash": "abcd"}, "Content/x.md": {"hash": ""}})
    loaded = hw._load_saved(storage)
    assert loaded == {"Content/Verse/w.verse": {"hash": "abcd", "mtime_ns": 0, "size": 0, "full": ""}}


def test_import_legacy_ledger_and_history(tmp_path: Path, island: Path) -> None:
    from backend.store.importers import phase3
    from frontend.ui_web.verse_editor import file_history as fh

    appdata = tmp_path / ".ducky-appdata" / "UEFN-Ducky"
    slug = "Island_deadbeef"
    led = appdata / "changesets" / slug
    (led / "runs").mkdir(parents=True)
    (led / "blobs").mkdir()
    (led / "blobs" / "aaaa000000000001.txt").write_text("before", encoding="utf-8")
    (led / "blobs" / "aaaa000000000002.txt").write_text("after", encoding="utf-8")
    run = {
        "schema_version": 1, "run_id": "r-legacy", "conv_id": "c1", "group_id": "", "started": 1.0, "ended": 2.0,
        "status": "done", "archived": False, "seen": {"Content/Verse/a.verse": {"hash": "aaaa000000000001", "ts": 1.0}},
        "entries": [{"seq": 1, "ts": 1.5, "path": "Content/Verse/a.verse", "op": "write", "outcome": "ok",
                     "before_blob": "aaaa000000000001", "after_blob": "aaaa000000000002",
                     "before_hash": "aaaa000000000001", "after_hash": "aaaa000000000002"}],
    }
    (led / "runs" / "r-legacy.json").write_text(json.dumps(run), encoding="utf-8")
    (led / "index.json").write_text(json.dumps({"Content/Verse/a.verse": {"hash": "aaaa000000000002", "run_id": "r-legacy", "ts": 1.5}}))
    (led / "human_index.json").write_text(json.dumps({"Content/Verse/b.verse": "bbbb000000000001"}))
    hist = appdata / "file_history" / slug / "Content" / "Verse" / "a.verse"
    hist.mkdir(parents=True)
    (hist / "1700000000000.json").write_text(json.dumps({"id": "1700000000000", "path": "Content/Verse/a.verse", "saved_at": 5, "content": "v1 text"}))
    (hist / "1700000000001.json").write_text(json.dumps({"schema_version": 2, "id": "1700000000001", "path": "Content/Verse/a.verse", "saved_at": 6, "content": "v2 text", "source": "agent", "ducky_name": "Coder"}))

    phase3.ensure()
    rep = phase3.report()
    assert rep["runs"] == 1 and rep["entries"] == 1 and rep["blobs"] == 2 and rep["versions"] == 2
    j = FileChangeJournal(lambda _root: appdata / "changesets" / slug)
    back = j.get_run("r-legacy", project_root=str(island))
    assert back["entries"][0]["after_blob"] == "aaaa000000000002"
    assert j.entry_contents("r-legacy", 1, project_root=str(island)) == {"path": "Content/Verse/a.verse", "before": "before", "after": "after"}
    assert j.index_stamp("Content/Verse/a.verse", project_root=str(island))["run_id"] == "r-legacy"
    assert ledger_repo.watch_get(slug) == {"Content/Verse/b.verse": "bbbb000000000001"}
    rows = fh.list_entries("Content/Verse/a.verse", str(island)) if False else None  # slug differs; use repo
    from backend.store.repos import history

    versions = history.list_rows(slug, "Content/Verse/a.verse")
    assert [v["entry_id"] for v in versions] == ["1700000000001", "1700000000000"]
    assert versions[0]["ducky_name"] == "Coder" and versions[1]["schema_version"] == 1
    assert history.content(slug, "Content/Verse/a.verse", "1700000000000") == "v1 text"
    assert not (led / "runs").exists() and (appdata / "legacy" / "changesets" / slug / "runs" / "r-legacy.json").exists()
    assert not (appdata / "file_history" / slug).exists()
    del rows
