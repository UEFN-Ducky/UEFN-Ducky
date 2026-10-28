"""The panel's changeset list ships summary rows; the run's detail stays whole."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from backend.workspace import runtime
from backend.workspace.identity import RunContext
from backend.workspace.journal import OP_EDITOR, FileChangeJournal
from backend.workspace.paths import content_hash
from backend.workspace.policy import ALLOW
from backend.workspace.writer import ProjectWriter, WriteRecord
from frontend.settings import PanelSettings, replace
from frontend.ui_web import changesets_api

# About what a real editor row carries (ExampleProject ledger: ~2.9 KB of params each).
PARAMS = {"node_id": "1.2", "status": "completed", "properties": {f"Prop{i}": "x" * 40 for i in range(50)}}


@pytest.fixture
def ledger(tmp_path: Path):
    root = tmp_path / "Proj"
    (root / "Content").mkdir(parents=True)
    replace(PanelSettings.load(), uefn_project_root=str(root)).save()
    journal = FileChangeJournal(lambda _r: tmp_path / "store")
    runtime.reset_for_tests(ProjectWriter.for_root(str(root), journal=journal))
    for r in range(50):
        ctx = RunContext(run_id=f"run{r}", conv_id=f"conv{r % 10}", ducky_name="D")
        for e in range(20):
            editor = e % 2 == 0
            after = json.dumps({"params": PARAMS}) if editor else f"line {e}\n" * 20
            journal.record(WriteRecord(
                op=OP_EDITOR if editor else "write",
                path=f"editor:actor{e}" if editor else f"Content/Verse/f{e}.verse",
                from_path="", before="", after=after, before_hash="", after_hash=content_hash(after),
                existed_before=False, tool="set_device_property" if editor else "workspace_write_file",
                writer=ctx.as_writer(tool="t"), ctx=ctx, ts=time.time(), lines_added=20, lines_removed=0,
                decision=ALLOW, project_root=str(root), abs_path=str(root / f"Content/Verse/f{e}.verse"),
                editor={"command": "set_device_property", "kind": "device", "program": "uefn", "summary": "Set",
                        "params": PARAMS, "inverse": {"command": "set", "params": {"Prop1": "old"}},
                        "targets": [f"Device_{e}"], "revertable": "auto"} if editor else None,
            ))
    yield journal
    runtime.reset_for_tests(None)


def test_list_is_summary_rows_under_200kb(ledger) -> None:
    full = ledger.list_runs(project_root=changesets_api._project_root(), limit=50)
    rows = changesets_api.list_changesets(limit=50)
    before, after = len(json.dumps(full)), len(json.dumps(rows))
    print(f"list_changesets 50 runs x 20 entries: {before / 1024:.0f} KB -> {after / 1024:.0f} KB")
    assert len(rows) == 50 and all(len(r["entries"]) == 20 for r in rows)
    assert after < 450 * 1024  # ~430 B per entry: every key left is one the list reads
    entry = next(e for e in rows[0]["entries"] if e.get("editor"))
    # What the list renders stays: summary, revertability, plan facts, diff presence.
    assert entry["editor"]["summary"] == "Set" and entry["editor"]["revertable"] == "auto"
    assert entry["editor"]["params"] == {"node_id": "1.2", "status": "completed"}
    assert "inverse" not in entry["editor"] and "abs_path" not in entry and "seen" not in rows[0]
    assert entry["after_blob"]


def test_get_changeset_keeps_the_full_run(ledger) -> None:
    run = changesets_api.get_changeset("run0")
    entry = next(e for e in run["entries"] if e["editor"])
    assert entry["editor"]["params"] == PARAMS and entry["editor"]["inverse"]
    assert entry["abs_path"] and "seen" in run
