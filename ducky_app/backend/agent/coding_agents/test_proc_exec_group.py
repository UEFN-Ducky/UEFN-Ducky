"""proc_exec — new_process_group for CTRL_BREAK, and get_process(conv_id) while the CLI runs."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from backend.agent.coding_agents import proc_exec


class _Started(Exception):
    pass


def _flags(monkeypatch, **kw) -> int:
    seen = {}

    def fake_popen(argv, **kwargs):
        seen.update(kwargs)
        raise _Started

    monkeypatch.setattr(proc_exec.subprocess, "Popen", fake_popen)
    with pytest.raises(_Started):
        proc_exec.run_streaming_process(argv=["x"], cwd=".", env_extra={}, conv_id="g", on_line=lambda s: None,
                                        timeout_s=1, **kw)
    return seen.get("creationflags", 0)


@pytest.mark.skipif(os.name != "nt", reason="process groups are a Windows creation flag")
def test_new_process_group_adds_the_flag_only_when_asked(monkeypatch):
    group = subprocess.CREATE_NEW_PROCESS_GROUP
    assert _flags(monkeypatch, new_process_group=True) & group
    assert not _flags(monkeypatch) & group
    assert _flags(monkeypatch, new_process_group=True) & subprocess.CREATE_NO_WINDOW


def test_get_process_is_the_live_popen_during_the_run_and_none_after():
    seen = []

    def on_line(line):
        seen.append(proc_exec.get_process("live-proc"))

    result = proc_exec.run_streaming_process(
        # The child stays up after its line: one that exited at once could be reaped before the
        # reader thread ran on_line, so get_process rightly said None (a Mac runner).
        argv=[sys.executable, "-c", "import time; print('hi', flush=True); time.sleep(1)"], cwd=".", env_extra={},
        conv_id="live-proc",
        on_line=on_line, timeout_s=20, new_process_group=True)
    assert result.returncode == 0
    assert isinstance(seen[0], subprocess.Popen)
    assert proc_exec.get_process("live-proc") is None
    assert proc_exec.get_process("never-ran") is None
