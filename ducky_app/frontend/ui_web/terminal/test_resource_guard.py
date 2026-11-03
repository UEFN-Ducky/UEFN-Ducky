"""An agent terminal is memory-capped and below normal priority; going over the cap is told."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from backend.sandbox import jobs
from frontend.ui_web.terminal import resource_guard
from frontend.ui_web.terminal.manager import TerminalManager
from frontend.ui_web.terminal.session import _tracking_job

windows = pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects")


def test_cap_is_a_share_of_the_pc_with_a_floor() -> None:
    assert resource_guard.memory_cap_mb(65536) == int(65536 * resource_guard.CAP_SHARE)
    assert resource_guard.memory_cap_mb(8192) == resource_guard.CAP_FLOOR_MB
    assert resource_guard.memory_cap_mb(2048) == 2048
    assert resource_guard.total_memory_mb() > 1024


def test_hit_cap_only_when_the_peak_rose_to_it() -> None:
    assert resource_guard.hit_cap(1000, 4000, 4096)
    assert not resource_guard.hit_cap(4000, 4000, 4096)  # an earlier command's peak
    assert not resource_guard.hit_cap(100, 2000, 4096)
    assert not resource_guard.hit_cap(100, 9000, 0)


@windows
def test_a_guarded_job_holds_cap_and_priority() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.readline()"], stdin=subprocess.PIPE)
    try:
        job = _tracking_job(child.pid, guarded=True)
        assert job is not None
        flags, priority, cap = job.limits()
        assert flags & jobs._LIMIT_JOB_MEMORY and flags & jobs._LIMIT_PRIORITY_CLASS
        assert priority == jobs._BELOW_NORMAL_PRIORITY_CLASS
        assert cap == resource_guard.memory_cap_mb()
        assert not flags & jobs._KILL_ON_JOB_CLOSE  # track only: closing Ducky's handle kills nothing
        job.close()
        plain = _tracking_job(child.pid)
        assert plain is not None and plain.limits()[0] == 0  # the person's own terminal
        plain.close()
    finally:
        child.communicate(b"\n", timeout=10)


@windows
def test_a_program_over_the_cap_is_refused_memory() -> None:
    hog = ("import sys; sys.stdin.readline()\n"
           "try:\n    b = bytearray(256 * 1024 * 1024); print('got it')\n"
           "except MemoryError:\n    print('refused')\n")
    job = jobs._Job(128, track_only=True, below_normal=True)
    child = subprocess.Popen([sys.executable, "-c", hog], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        assert job.assign_pid(child.pid)
        out, _ = child.communicate(b"\n", timeout=30)
    finally:
        job.close()
    assert out.decode().strip() == "refused"


class _Capped:
    """A terminal whose memory peak climbs to its cap while the command runs."""

    cwd = "."
    shell = "bash"

    def __init__(self, peaks: list[int], cap: int) -> None:
        self._peaks, self._cap = list(peaks), cap

    def memory_peak_and_cap_mb(self) -> tuple[int, int]:
        return (self._peaks.pop(0) if len(self._peaks) > 1 else self._peaks[0]), self._cap

    def run_command(self, command: str, **_kw) -> dict:
        return {"ok": True, "exit_code": 101, "output_tail": "memory allocation of 1048576 bytes failed"}

    def is_busy(self) -> bool:
        return False


def test_the_agent_is_told_it_hit_the_cap() -> None:
    ran = TerminalManager._run_watched(_Capped([500, 4000], 4096), "cargo build", 5)  # type: ignore[arg-type]
    assert "memory cap" in ran["hint"] and "Do not retry it as is" in ran["hint"]
    quiet = TerminalManager._run_watched(_Capped([500, 900], 4096), "cargo build", 5)  # type: ignore[arg-type]
    assert "hint" not in quiet
