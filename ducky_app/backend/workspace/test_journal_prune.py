"""FileChangeJournal retention: prune drops old closed runs and the blobs only they used."""

from __future__ import annotations

from backend.workspace import identity
from backend.workspace.test_journal import as_run, env  # noqa: F401 — env is a pytest fixture


def test_prune_drops_old_closed_runs_and_orphan_blobs(env) -> None:  # noqa: F811
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
