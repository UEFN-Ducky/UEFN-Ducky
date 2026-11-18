"""Turn-end retention on the database: decided from run columns, no run document parsed."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.store.repos import ledger as ledger_repo
from backend.workspace.journal import FileChangeJournal

NOW = 1_000_000_000.0
PID = "Island_abc"


@pytest.fixture
def journal(tmp_path: Path, monkeypatch) -> FileChangeJournal:
    monkeypatch.setenv("DUCKY_STORE_BACKEND", "db")
    return FileChangeJournal(lambda _root: tmp_path / "ledger" / PID, clock=lambda: NOW)


def _put_runs(count: int) -> None:
    for i in range(count):
        ledger_repo.run_put(PID, {
            "run_id": f"run{i:03d}",
            "conv_id": f"c{i}",
            "started": NOW - i * 3 * 3600.0,  # 320 runs span 40 days
            "status": "running" if i % 53 == 0 else "done",
            "archived": i % 37 == 0,
            "entries": [{"seq": 1, "path": f"f{i}.txt", "ts": NOW - i, "outcome": "ok"}],
            "seen": {f"f{i}.txt": {"hash": "x"}},
        })


def _old_dropped(docs: list[dict], *, max_age_days: int = 30, keep_runs: int = 300) -> set[str]:
    """The retention rule as it was, over full run documents."""
    cutoff = NOW - max_age_days * 86400.0
    runs = sorted(((float(r.get("started") or 0.0), r) for r in docs), key=lambda t: t[0], reverse=True)
    dropped = set()
    for i, (started, run) in enumerate(runs):
        if run.get("archived"):
            continue
        if run.get("status") != "running" and (i >= keep_runs or started < cutoff):
            dropped.add(run["run_id"])
    return dropped


def test_prune_parses_no_run_document(journal: FileChangeJournal, monkeypatch) -> None:
    _put_runs(320)
    before = ledger_repo.runs_docs(PID)
    expected_dropped = _old_dropped(before)
    assert expected_dropped  # the fixture has runs past both limits

    def _no_parse(*_a, **_k):
        raise AssertionError("prune parsed a run document")

    monkeypatch.setattr(ledger_repo, "_assemble", _no_parse)
    stats = journal.prune(project_root="Island")
    kept = set(ledger_repo.catalog(PID))
    assert stats["removed_runs"] == len(expected_dropped)
    assert kept == {r["run_id"] for r in before} - expected_dropped


def test_no_sweep_when_nothing_was_pruned(journal: FileChangeJournal, monkeypatch) -> None:
    _put_runs(5)  # all young, under the run limit
    sweeps: list[int] = []
    real_sweep = ledger_repo.sweep_blobs
    monkeypatch.setattr(ledger_repo, "sweep_blobs", lambda: sweeps.append(1) or real_sweep())
    assert journal.prune(project_root="Island") == {"removed_runs": 0, "removed_blobs": 0}
    assert sweeps == []
    journal.prune(project_root="Island", keep_runs=1)  # drops the closed, unarchived older runs
    assert sweeps == [1]


def test_the_sweep_plan_uses_three_indexes(journal: FileChangeJournal) -> None:
    from backend.store import db

    sql = db.connect().execute(
        "EXPLAIN QUERY PLAN DELETE FROM blobs WHERE hash NOT IN (SELECT before_blob FROM run_entries WHERE before_blob<>'') "
        "AND hash NOT IN (SELECT after_blob FROM run_entries WHERE after_blob<>'') "
        "AND hash NOT IN (SELECT content_hash FROM file_versions)"
    ).fetchall()
    plan = " ".join(str(row[-1]) for row in sql)
    for index in ("ix_entries_before_blob", "ix_entries_after_blob", "ix_file_versions_hash"):
        assert index in plan, plan


def test_runs_brief_reads_columns_newest_first(journal: FileChangeJournal) -> None:
    _put_runs(3)
    assert ledger_repo.runs_brief(PID) == [
        {"run_id": "run000", "started": NOW, "status": "running", "archived": True},
        {"run_id": "run001", "started": NOW - 3 * 3600.0, "status": "done", "archived": False},
        {"run_id": "run002", "started": NOW - 6 * 3600.0, "status": "done", "archived": False},
    ]
