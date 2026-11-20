"""Run files are parsed once and read again only when they change.

Nov 11 2026: every call of ``runs()`` read and parsed every run of the last day, and the
window, the team keeper and the updater all call it on a timer."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from backend.automations import live_runs


@pytest.fixture
def folder(monkeypatch, tmp_path: Path):
    directory = tmp_path / "workflow-runs"
    monkeypatch.setattr(live_runs, "_directory", lambda: directory)
    monkeypatch.setattr(live_runs, "_PARSED", {})
    reads: list[str] = []
    real = Path.read_text

    def read_text(self, *args, **kwargs):
        if self.parent == directory:
            reads.append(self.name)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    return directory, reads


def _event(run: str, state: str = "started", **more) -> dict:
    return {"type": "workflow_run", "id": "wf", "run": run, "state": state, **more}


def test_an_unchanged_run_file_is_not_read_again(folder) -> None:
    directory, reads = folder
    assert live_runs.runs() == []  # no folder yet
    live_runs.start("wf", "r1")
    live_runs.start("wf", "r2")
    live_runs.record(_event("r1"))
    reads.clear()
    first = live_runs.runs()
    assert sorted(r["run"] for r in first) == ["r1", "r2"] and len(reads) == 2
    reads.clear()
    for _ in range(50):
        assert sorted(r["run"] for r in live_runs.runs("wf")) == ["r1", "r2"]
        assert live_runs.running("wf") and len(live_runs.snapshot()) == 1
    assert reads == []  # 150 calls, no file read


def test_a_change_from_any_process_is_seen_on_the_next_call(folder) -> None:
    directory, reads = folder
    live_runs.start("wf", "r1")
    assert live_runs.runs()[0]["state"] == "started"
    live_runs.record(_event("r1", "ok"))  # this process
    reads.clear()
    assert live_runs.runs()[0]["state"] == "ok" and len(reads) == 1
    # Another process writes the file its own way: only its time and size say so.
    path = live_runs._path("wf", "r1")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["state"] = "error"
    data["events"].append(_event("r1", "error", error="It failed, with a long enough reason to change the size."))
    path.write_text(json.dumps(data), encoding="utf-8")
    reads.clear()
    got = live_runs.runs()[0]
    assert got["state"] == "error" and len(got["events"]) == 2 and len(reads) == 1


def test_what_a_caller_changes_never_reaches_the_next_caller(folder) -> None:
    live_runs.start("wf", "r1")
    live_runs.record(_event("r1"))
    got = live_runs.runs()[0]
    got["state"] = "mine"
    got["events"].append({"type": "made up"})
    again = live_runs.runs()[0]
    assert again["state"] == "started" and [e["type"] for e in again["events"]] == ["workflow_run"]


def test_a_run_whose_process_ended_reads_stopped_without_touching_the_file(folder, monkeypatch) -> None:
    directory, reads = folder
    live_runs.start("wf", "r1")
    assert live_runs.runs()[0]["state"] == "started"
    monkeypatch.setattr(live_runs, "_alive", lambda data: False)
    reads.clear()
    for _ in range(3):  # decided on every call: the file never says the process is gone
        got = live_runs.runs()[0]
        assert got["state"] == "stopped" and got["events"][-1]["error"] == "The process running this workflow ended."
        assert len(got["events"]) == 1
    assert reads == [] and not live_runs.running("wf")
    assert json.loads(live_runs._path("wf", "r1").read_text(encoding="utf-8"))["state"] == "started"


def test_a_removed_or_day_old_run_is_dropped(folder) -> None:
    directory, reads = folder
    live_runs.start("wf", "r1")
    live_runs.start("wf", "r2")
    assert len(live_runs.runs()) == 2 and len(live_runs._PARSED) == 2
    live_runs._path("wf", "r1").unlink()
    assert [r["run"] for r in live_runs.runs()] == ["r2"] and len(live_runs._PARSED) == 1
    path = live_runs._path("wf", "r2")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["started"] = time.time() - 2 * 86400
    path.write_text(json.dumps(data), encoding="utf-8")
    path.with_suffix(".stop").touch()
    assert live_runs.runs() == [] and live_runs._PARSED == {}
    assert not path.exists() and not path.with_suffix(".stop").exists()


def test_a_half_written_file_is_left_out_and_read_again_next_time(folder) -> None:
    directory, reads = folder
    live_runs.start("wf", "r1")
    path = live_runs._path("wf", "r1")
    whole = path.read_text(encoding="utf-8")
    path.write_text(whole[: len(whole) // 2], encoding="utf-8")
    assert live_runs.runs() == [] and live_runs._PARSED == {}
    path.write_text("[1, 2]", encoding="utf-8")  # not a run at all
    assert live_runs.runs() == []
    path.write_text(whole, encoding="utf-8")
    os.utime(path, None)
    assert [r["run"] for r in live_runs.runs()] == ["r1"]
