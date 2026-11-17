"""A busy PC: freezes say what the PC was short of, the window keeps its priority, and a
start on a full graphics card draws without the card."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from backend.util.proc_table import Proc
from frontend import busy_pc, low_memory


@pytest.fixture
def pc(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(busy_pc, "note_path", lambda: tmp_path / "launcher" / "gpu.json")
    monkeypatch.setattr(busy_pc, "pc_now", {})
    monkeypatch.setattr(busy_pc, "raise_window_priority", lambda: 0)
    monkeypatch.setattr(low_memory, "last_gpu_share", None)
    return tmp_path


def test_a_long_freeze_is_recorded_with_the_pcs_last_sample(pc, monkeypatch) -> None:
    monkeypatch.setattr(low_memory, "last_gpu_share", 0.96)
    busy_pc.on_heartbeat({"free_gb": 12.2})
    meta = busy_pc.freeze_meta("ui_stall", 900.0)
    assert meta["pc_gpu"] == 0.96 and meta["pc_free_gb"] == 12.2
    assert busy_pc.freeze_meta("ui_stall", 60.0) == {}  # a short one: no extra columns
    assert busy_pc.freeze_meta("ui_frame", 900.0) == {}


def test_the_health_report_says_what_the_pc_was_short_of() -> None:
    rows = [{"pc_gpu": 0.96, "pc_cpu": 36.0, "pc_free_gb": 12.2}, {"pc_gpu": 0.97, "pc_cpu": 40.0, "pc_free_gb": 11.0},
            {"duration_ms": 300}]
    assert busy_pc.freeze_cause(rows) == ("During them the graphics card was 97% full, the CPU 40% busy "
                                          "and 12.2 GB of memory was free.")
    assert busy_pc.freeze_cause([{"pc_cpu": 91.0}]) == "During them the CPU 91% busy."
    assert busy_pc.freeze_cause([{"duration_ms": 300}]) == ""


def test_a_start_just_after_a_full_card_draws_without_it(pc, monkeypatch) -> None:
    assert busy_pc.browser_arguments() == busy_pc.BROWSER_ARGS  # no note yet
    monkeypatch.setattr(low_memory, "last_gpu_share", 0.96)
    busy_pc.on_heartbeat({})
    note = json.loads(busy_pc.note_path().read_text(encoding="utf-8"))
    assert note["share"] == 0.96 and abs(note["at"] - time.time()) < 5
    assert busy_pc.gpu_was_full() and busy_pc.browser_arguments().endswith(" --disable-gpu")
    assert not busy_pc.gpu_was_full(now=time.time() + busy_pc.NOTE_MAX_AGE_S + 1)  # hours later: the card again
    monkeypatch.setattr(low_memory, "last_gpu_share", 0.61)
    busy_pc.on_heartbeat({})
    assert not busy_pc.gpu_was_full() and busy_pc.browser_arguments() == busy_pc.BROWSER_ARGS


def test_an_unknown_card_writes_no_note(pc) -> None:
    busy_pc.on_heartbeat({"free_gb": 3.0})
    assert not busy_pc.note_path().exists() and "gpu" not in busy_pc.pc_now


def _proc(pid: int, ppid: int, name: str) -> Proc:
    return Proc(pid, ppid, name, 0, 0, 1, 1, 0.0, 0.0)


def test_only_the_windows_own_processes_are_raised() -> None:
    procs = {p.pid: p for p in (
        _proc(10, 1, "Ducky-Launcher.exe"), _proc(11, 10, "msedgewebview2.exe"), _proc(12, 11, "msedgewebview2.exe"),
        _proc(20, 10, "UEFN-Ducky.exe"), _proc(21, 20, "msedgewebview2.exe"), _proc(22, 20, "claude.exe"),
        _proc(30, 1, "msedgewebview2.exe"))}
    # Under the launcher: it, its WebView2 and the backend's browser panes; never the backend or an agent.
    assert sorted(busy_pc.window_pids(procs, me=20, launcher=10)) == [10, 11, 12, 21]
    # The Python window: this process and its WebView2.
    assert sorted(busy_pc.window_pids(procs, me=20)) == [20, 21]


@pytest.mark.skipif(os.name != "nt", reason="Windows priority classes")
def test_only_a_normal_priority_window_process_is_raised(monkeypatch) -> None:
    # Windows itself is faked: a test run inside a job with a priority limit can't raise anything.
    classes = {11: 0x20, 12: 0x40, 13: 0x8000}  # Normal, Idle (a hidden page), already Above Normal

    class K32:
        def OpenProcess(self, access, inherit, pid):
            return pid if pid in classes else None  # 14: gone, or not ours to open

        def GetPriorityClass(self, handle):
            return classes[handle]

        def SetPriorityClass(self, handle, value):
            classes[handle] = value
            return 1

        def CloseHandle(self, handle):
            return 1

    monkeypatch.setattr(busy_pc, "_kernel32", K32)
    monkeypatch.setattr(busy_pc, "window_pids", lambda *a, **k: [11, 12, 13, 14])
    assert busy_pc.raise_window_priority() == 1
    assert classes == {11: 0x8000, 12: 0x40, 13: 0x8000}
    assert busy_pc.raise_window_priority() == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows priority classes")
def test_the_real_windows_calls_reach_a_real_process(monkeypatch) -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        monkeypatch.setattr(busy_pc, "window_pids", lambda *a, **k: [child.pid])
        assert busy_pc.raise_window_priority() in (0, 1)  # 0 inside a job that pins the priority
    finally:
        child.kill()
