"""Changeset retention: which runs a project's ledger keeps, and listing run documents.

Split out of ``journal.py``; ``FileChangeJournal.prune`` / ``_iter_run_docs`` call these.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.workspace import journal as _j

if TYPE_CHECKING:
    from backend.workspace.journal import FileChangeJournal


def iter_run_docs(journal: FileChangeJournal, storage: Path) -> list[dict[str, Any]]:
    if _j._use_db():
        return _j._repo().runs_docs(_j._pid(storage))
    docs: list[dict[str, Any]] = []
    for path in (storage / "runs").glob("*.json"):
        run = journal._read_json(path)
        if isinstance(run, dict) and run.get("run_id"):
            docs.append(run)
    return docs


def _retention_rows(journal: FileChangeJournal, storage: Path) -> list[dict[str, Any]]:
    """Runs with at least run_id, started, status and archived. The database answers from
    columns (no run document is parsed); the files backend needs the documents for its blob refs."""
    if _j._use_db():
        return _j._repo().runs_brief(_j._pid(storage))
    return iter_run_docs(journal, storage)


def prune(journal: FileChangeJournal, *, project_root: str, max_age_days: int = 30, keep_runs: int = 300) -> dict[str, int]:
    storage = journal._storage(project_root)
    cutoff = journal._clock() - max(1, int(max_age_days)) * 86400.0
    removed_runs = 0
    with journal._lock(storage):
        runs: list[tuple[float, dict[str, Any]]] = [
            (float(run.get("started") or 0.0), run) for run in _retention_rows(journal, storage)
        ]
        runs.sort(key=lambda t: t[0], reverse=True)
        keep: list[dict[str, Any]] = []
        dropped: set[str] = set()
        for i, (started, run) in enumerate(runs):
            open_run = run.get("status") in _j._OPEN_STATUSES
            if run.get("archived"):
                keep.append(run)
                continue
            if not open_run and (i >= keep_runs or started < cutoff):
                journal._delete_run_doc(storage, run)
                rid = str(run.get("run_id") or "")
                if rid:
                    dropped.add(rid)
                removed_runs += 1
                continue
            keep.append(run)
        removed_blobs = 0
        if removed_runs:
            # Nothing dropped, nothing newly orphaned: the sweep (a write transaction) waits
            # for a turn that drops a run. A manual sweep is in Settings > Database.
            journal._drop_catalog_ids(storage, dropped)
            removed_blobs = journal._sweep_orphan_blobs(storage, keep)
    return {"removed_runs": removed_runs, "removed_blobs": removed_blobs}
