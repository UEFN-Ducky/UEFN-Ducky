"""Project ownership must be checked in the existing ledger write transaction."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from backend.store import db
from backend.store.repos import ledger


def run(run_id="shared", value="original"):
    return {"run_id": run_id, "conv_id": value, "status": "running",
            "entries": [{"seq": 1, "path": "Content/a.txt", "outcome": "ok", "value": value}],
            "seen": {"Content/a.txt": {"hash": value}}}


def test_collision_preserves_document_entries_seen_and_sanitizes_error():
    ledger.run_put("private-first-project", run())
    original = ledger.run_get("private-first-project", "shared")
    with pytest.raises(ValueError) as error:
        ledger.run_put("private-second-project", run(value="replacement"))
    assert str(error.value) == "Changeset run belongs to another project."
    assert ledger.run_get("private-first-project", "shared") == original
    assert ledger.run_get("private-second-project", "shared") is None


def test_same_project_upsert_still_replaces_entries_and_seen():
    ledger.run_put("project", run())
    changed = run(value="updated")
    changed["entries"].append({"seq": 2, "path": "Content/b.txt", "outcome": "ok"})
    changed["seen"] = {"Content/b.txt": {"hash": "new"}}
    ledger.run_put("project", changed)
    assert ledger.run_get("project", "shared") == changed


def test_distinct_runs_coexist_across_projects():
    first, second = run("first"), run("second", "second")
    ledger.run_put("p1", first)
    ledger.run_put("p2", second)
    assert ledger.run_get("p1", "first") == first
    assert ledger.run_get("p2", "second") == second
    assert ledger.run_get("p2", "first") is None


def test_concurrent_first_claim_has_one_winner_and_intact_payload():
    db.connect()  # Complete migrations before independent thread connections.
    start = Barrier(2)

    def claim(project):
        try:
            db.connect()
            start.wait(timeout=10)
            try:
                ledger.run_put(project, run(value=project))
            except ValueError as exc:
                assert str(exc) == "Changeset run belongs to another project."
                return project, False
            return project, True
        finally:
            db.close_thread_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("p1", "p2")))
    assert sorted(ok for _, ok in results) == [False, True]
    for project, won in results:
        assert ledger.run_get(project, "shared") == (run(value=project) if won else None)
