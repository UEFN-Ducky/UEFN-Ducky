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
        assert cap >= resource_guard.SHARE_FLOOR_MB
        assert not flags & jobs._KILL_ON_JOB_CLOSE  # track only: closing Ducky's handle kills nothing
        assert job in jobs.shared_jobs()  # its cap follows the shared budget
        assert job.set_idle(True) and job.limits()[1] == jobs._IDLE_PRIORITY_CLASS
        assert job.set_idle(False) and job.limits()[1] == jobs._BELOW_NORMAL_PRIORITY_CLASS
        job.close()
        assert job not in jobs.shared_jobs()
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


@windows
def test_a_running_jobs_cap_can_be_lowered() -> None:
    hog = ("import sys; sys.stdin.readline()\n"
           "try:\n    b = bytearray(256 * 1024 * 1024); print('got it')\n"
           "except MemoryError:\n    print('refused')\n")
    job = jobs._Job(1024, track_only=True, below_normal=True)
    child = subprocess.Popen([sys.executable, "-c", hog], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        assert job.assign_pid(child.pid)
        assert job.set_memory_limit(128)  # lowered while the child runs
        assert job.limits()[2] == 128 and job.limits()[0] & jobs._LIMIT_PRIORITY_CLASS
        out, _ = child.communicate(b"\n", timeout=30)
    finally:
        job.close()
    assert out.decode().strip() == "refused"


GB = 1024


def test_budget_leaves_room_for_the_pc_and_uefn() -> None:
    # 63 GB RAM + 38 GB page file, 32 GB committed (UEFN 24 GB of it), no agent runs yet.
    budget = resource_guard.budget_mb(commit_limit_mb=101 * GB, commit_used_mb=32 * GB, agent_used_mb=0,
                                      ram_mb=63 * GB, uefn_mb=24 * GB)
    assert budget == 101 * GB - 32 * GB - int(63 * GB * 0.10) - 6 * GB
    # What agent runs hold already is theirs to share, not "outside" memory.
    assert resource_guard.budget_mb(commit_limit_mb=101 * GB, commit_used_mb=42 * GB, agent_used_mb=10 * GB,
                                    ram_mb=63 * GB, uefn_mb=24 * GB) == budget
    # No UEFN: no headroom kept for it; small PC: reserve floor 4 GB; never negative.
    assert resource_guard.budget_mb(commit_limit_mb=20 * GB, commit_used_mb=8 * GB, agent_used_mb=0,
                                    ram_mb=16 * GB) == 8 * GB
    assert resource_guard.budget_mb(commit_limit_mb=8 * GB, commit_used_mb=8 * GB, agent_used_mb=0,
                                    ram_mb=8 * GB, uefn_mb=6 * GB) == 0


class _FakeJob:
    def __init__(self, ceiling: int = 0) -> None:
        self.memory_mb, self.ceiling_mb, self.set_to = 4096, ceiling, []

    def set_memory_limit(self, mb: int) -> bool:
        self.memory_mb = mb
        self.set_to.append(mb)
        return True


def test_three_jobs_share_the_budget() -> None:
    three = [_FakeJob(), _FakeJob(), _FakeJob(ceiling=2 * GB)]
    split = resource_guard.rebalance(three, numbers={"budget_mb": 30 * GB, "uefn_mb": 0})
    assert split["share_mb"] == 10 * GB and split["jobs"] == 3
    assert [j.memory_mb for j in three] == [10 * GB, 10 * GB, 2 * GB]  # a sandbox program keeps its ceiling
    assert resource_guard.last_split()["share_mb"] == 10 * GB


def test_shares_shrink_when_uefn_grows() -> None:
    def share(uefn_mb: int) -> int:
        budget = resource_guard.budget_mb(commit_limit_mb=101 * GB, commit_used_mb=8 * GB + uefn_mb,
                                          agent_used_mb=0, ram_mb=63 * GB, uefn_mb=uefn_mb)
        two = [_FakeJob(), _FakeJob()]
        resource_guard.rebalance(two, numbers={"budget_mb": budget, "uefn_mb": uefn_mb})
        return two[0].memory_mb

    assert share(0) > share(12 * GB) > share(24 * GB) > share(48 * GB)


def test_every_share_is_at_least_a_gigabyte() -> None:
    four = [_FakeJob() for _ in range(4)]
    resource_guard.rebalance(four, numbers={"budget_mb": 500, "uefn_mb": 30 * GB})
    assert all(j.memory_mb == resource_guard.SHARE_FLOOR_MB for j in four)
    assert resource_guard.split_mb(0, 0) == resource_guard.SHARE_FLOOR_MB


def test_hint_names_the_budget_and_uefn() -> None:
    hint = resource_guard.cap_hint(3900, 4096, 65536, {"budget_mb": 20 * GB, "uefn_mb": 24 * GB, "jobs": 5})
    assert "20.0 GB across 5" in hint and "UEFN (24.0 GB)" in hint and "pytest -n 2" in hint


def test_heavy_runs_go_idle_beside_uefn(monkeypatch) -> None:
    from frontend.ui_web.terminal import manager

    class _Term:
        agent = True
        idle: list[bool] = []

        def set_idle(self, idle: bool) -> bool:
            self.idle.append(idle)
            return True

    assert resource_guard.is_heavy("py -3 -m pytest -q -n auto") and resource_guard.is_heavy("npm run build")
    assert not resource_guard.is_heavy("git status")
    monkeypatch.setattr(resource_guard, "uefn_private_mb", lambda names=None: 20 * GB)
    term = _Term()
    assert manager._idle_beside_uefn(term, "cargo test") and term.idle == [True]
    assert not manager._idle_beside_uefn(term, "git log")
    monkeypatch.setattr(resource_guard, "uefn_private_mb", lambda names=None: 0)
    assert not manager._idle_beside_uefn(term, "cargo test")


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
