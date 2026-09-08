"""Agent stop closes the run's changeset ledger with a mapped status."""

from __future__ import annotations

from pathlib import Path

from backend.workspace import identity, runtime
from backend.workspace.identity import RunContext
from backend.workspace.journal import FileChangeJournal
from backend.workspace.writer import ProjectWriter
from frontend.ui_web import agent_modes


def test_close_changeset_run_maps_reason_to_status(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "Proj"
    (root / "Content").mkdir(parents=True)
    journal = FileChangeJournal(lambda _r: tmp_path / "store")
    runtime.reset_for_tests(ProjectWriter.for_root(str(root), journal=journal))
    monkeypatch.setattr("backend.bridge.workspace_roots", lambda: [str(root)])
    try:
        token = identity.bind(RunContext(run_id="r1", conv_id="c1"))
        try:
            runtime.get_writer().write_text("Content/a.txt", "x")
        finally:
            identity.reset(token)
        agent_modes.close_changeset_run("r1", "stopped")
        assert journal.get_run("r1", project_root=str(root))["status"] == "cancelled"
        agent_modes.close_changeset_run("missing", "done")  # no ledger: silent
        agent_modes.close_changeset_run("", "done")
    finally:
        runtime.reset_for_tests(None)
