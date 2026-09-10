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


def test_close_changeset_run_uses_writer_root_not_first_workspace(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "CardGame"
    (root / "Content").mkdir(parents=True)
    journal = FileChangeJournal(lambda _r: tmp_path / "store")
    runtime.reset_for_tests(ProjectWriter.for_root(str(root), journal=journal))
    monkeypatch.setattr("backend.bridge.workspace_roots", lambda: [str(tmp_path / "other")])
    try:
        token = identity.bind(RunContext(run_id="r1", conv_id="c1"))
        try:
            runtime.get_writer().write_text("Content/a.txt", "x")
        finally:
            identity.reset(token)
        agent_modes.close_changeset_run("r1", "done")
        assert journal.get_run("r1", project_root=str(root))["status"] == "done"
    finally:
        runtime.reset_for_tests(None)


def test_revert_refuses_while_agent_is_live(tmp_path: Path, monkeypatch) -> None:
    from frontend.ui_web import changesets_api

    root = tmp_path / "Proj"
    (root / "Content").mkdir(parents=True)
    journal = FileChangeJournal(lambda _r: tmp_path / "store")
    runtime.reset_for_tests(ProjectWriter.for_root(str(root), journal=journal))
    monkeypatch.setattr(changesets_api, "_project_root", lambda: str(root))
    monkeypatch.setattr(changesets_api, "_journal", lambda: journal)
    try:
        token = identity.bind(RunContext(run_id="r-live", conv_id="c-live"))
        try:
            runtime.get_writer().write_text("Content/a.txt", "x")
        finally:
            identity.reset(token)
        monkeypatch.setattr(agent_modes, "live_changeset_run_ids", lambda: frozenset({"r-live"}))
        monkeypatch.setattr(agent_modes, "is_agent_running", lambda cid: cid == "c-live")
        blocked = changesets_api.revert_run("r-live")
        assert blocked["ok"] is False
        assert "Stop the agent first" in blocked["errors"][0]
        assert blocked["reverted"] == []
        monkeypatch.setattr(agent_modes, "live_changeset_run_ids", lambda: frozenset())
        monkeypatch.setattr(agent_modes, "is_agent_running", lambda _cid: False)
        ok = changesets_api.revert_run("r-live")
        assert ok.get("ok") is True or ok.get("reverted") is not None
    finally:
        runtime.reset_for_tests(None)
