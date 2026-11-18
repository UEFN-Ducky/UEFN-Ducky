"""run_streaming_process waits on the CLI instead of polling it ten times a second."""

from __future__ import annotations

import subprocess
import sys
import time
import uuid

from backend.agent.coding_agents import proc_exec


def _run(code: str, on_line=lambda line: None):
    return proc_exec.run_streaming_process(
        argv=[sys.executable, "-c", code], cwd="", env_extra={}, conv_id=f"t-{uuid.uuid4().hex[:8]}",
        on_line=on_line, timeout_s=0,
    )


def test_a_running_cli_is_checked_at_most_twice_a_second(monkeypatch):
    waits: list[float | None] = []
    real_wait = subprocess.Popen.wait

    def counting_wait(self, timeout=None):
        waits.append(timeout)
        return real_wait(self, timeout=timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", counting_wait)
    result = _run("import time; time.sleep(2.1)")
    assert result.returncode == 0
    assert len(waits) <= 6  # 2.1 s at the old 0.1 s poll: 21


def test_the_cli_exiting_is_seen_within_150_ms():
    seen: list[float] = []
    result = _run("print('bye', flush=True)", on_line=lambda line: seen.append(time.monotonic()))
    returned = time.monotonic()
    assert result.returncode == 0 and seen
    assert returned - seen[0] < 0.15
