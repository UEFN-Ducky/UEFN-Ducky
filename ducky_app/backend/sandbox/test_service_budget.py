"""Sandbox programs share the memory budget agent terminals have, with room kept for UEFN."""

from __future__ import annotations

import os
import sys

import pytest

from backend.sandbox import jobs, service
from frontend.ui_web.terminal import resource_guard


def test_a_launch_cap_never_exceeds_its_budget_share(monkeypatch) -> None:
    monkeypatch.setattr(resource_guard, "new_job_cap_mb", lambda: 2048)
    assert service._launch_cap(4096) == 2048
    monkeypatch.setattr(resource_guard, "new_job_cap_mb", lambda: 9000)
    assert service._launch_cap(4096) == 4096  # what was asked stays the most


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects")
def test_a_shared_program_joins_and_leaves_the_budget(tmp_path) -> None:
    run = jobs.Launched([sys.executable, "-c", "import time; time.sleep(30)"], cwd=str(tmp_path),
                        env=dict(os.environ), log=tmp_path / "out.log", memory_mb=2048, share=True)
    plain = jobs.Launched([sys.executable, "-c", "pass"], cwd=str(tmp_path), env=dict(os.environ),
                          log=tmp_path / "plain.log", memory_mb=2048)
    try:
        assert run.job in jobs.shared_jobs() and plain.job not in jobs.shared_jobs()
        run.job.ceiling_mb = 2048
        resource_guard.rebalance([run.job], numbers={"budget_mb": 1500, "uefn_mb": 0})
        assert run.job.limits()[2] == 1500  # lowered live to its share
    finally:
        run.stop()
        plain.stop()
    assert run.job not in jobs.shared_jobs()
