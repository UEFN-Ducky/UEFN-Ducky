"""stall_notice — a quiet turn names what it is waiting on."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.coding_agents.stall_notice import StallNotice


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(alive=True):
    clock = Clock()
    seen: list[dict] = []
    notice = StallNotice("conv-1", "run-1", label="Claude Code", after_s=60, every_s=10,
                         clock=clock, is_alive=lambda: alive)
    push = notice.wrap(seen.append)
    return notice, push, clock, seen


def test_quiet_under_limit_says_nothing():
    notice, push, clock, seen = make()
    push({"type": "text_delta", "text": "hi"})
    clock.now += 59
    assert notice.tick() == ""
    assert seen == [{"type": "text_delta", "text": "hi"}]


def test_pending_tool_is_named():
    notice, push, clock, seen = make()
    push({"type": "tool", "tool": {"name": "mcp__uefn__workspace_read_file", "status": "pending"}})
    clock.now += 370
    assert notice.tick() == "Waiting on workspace_read_file · 6m 10s, no answer yet"
    assert seen[-1] == {"type": "status", "text": "Waiting on workspace_read_file · 6m 10s, no answer yet",
                        "conv_id": "conv-1", "run_id": "run-1"}


def test_tool_done_closes_oldest_chip_first():
    notice, push, clock, _ = make()
    push({"type": "tool", "tool": {"name": "Read", "status": "pending"}})
    push({"type": "tool", "tool": {"name": "Grep", "status": "pending"}})
    push({"type": "tool_done", "tool": {"name": "Read", "status": "success"}})
    clock.now += 90
    assert notice.text().startswith("Waiting on Grep · 1m 30s")
    push({"type": "tool_done", "tool": {"name": "Grep", "status": "success"}})
    clock.now += 90
    assert notice.text() == "Waiting for the model · 1m 30s with no output (Claude Code is running)"


def test_dead_cli_is_reported():
    notice, push, clock, _ = make(alive=False)
    push({"type": "status", "text": "Claude Code initialized"})
    clock.now += 61
    assert notice.text() == "No output for 1m 01s: Claude Code has exited, finishing the turn"


def test_own_notice_does_not_reset_the_silence():
    notice, push, clock, _ = make()
    push({"type": "thinking", "text": "x"})
    clock.now += 60
    notice.tick()
    clock.now += 10
    assert notice.text().startswith("Waiting for the model · 1m 10s")


def test_setup_step_is_named_then_stopped_once():
    clock = Clock()
    seen: list[dict] = []
    stuck: list[str] = []
    notice = StallNotice("conv-1", "run-1", clock=clock, is_alive=lambda: False,
                         step_after_s=15, stuck_after_s=120, on_stuck=stuck.append)
    notice.wrap(seen.append)
    notice.set_step("finding Codex")
    clock.now += 14
    assert notice.tick() == ""
    clock.now += 6
    assert notice.tick() == "Still starting · finding Codex · 20s"
    clock.now += 100
    notice.tick()
    notice.tick()
    assert stuck == ["finding Codex"]


def test_login_step_is_never_stopped_and_cleared_step_waits_for_model():
    clock = Clock()
    stuck: list[str] = []
    notice = StallNotice("conv-1", clock=clock, is_alive=lambda: True, on_stuck=stuck.append)
    notice.wrap(lambda _e: None)
    notice.set_step("checking the Codex login", can_stop=False)
    clock.now += 600
    notice.tick()
    assert stuck == []
    notice.set_step("")
    clock.now += 30
    assert notice.text() == ""  # the CLI gets the normal 60 s before a notice
    clock.now += 30
    assert notice.text().startswith("Waiting for the model · 1m 00s")


def test_thread_pushes_and_stops():
    seen: list[dict] = []
    notice = StallNotice("conv-2", after_s=0, every_s=0.02, is_alive=lambda: True)
    notice.wrap(seen.append)
    notice.start()
    deadline = time.time() + 2
    while not seen and time.time() < deadline:
        time.sleep(0.01)
    notice.stop()
    count = len(seen)
    time.sleep(0.1)
    assert count >= 1 and seen[0]["type"] == "status"
    assert len(seen) == count
