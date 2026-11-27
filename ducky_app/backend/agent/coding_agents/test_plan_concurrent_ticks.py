"""Ticks from many seats at once are never lost (S2's 4-seat live test lost some)."""

from __future__ import annotations

import threading

import pytest

from backend.agent.coding_agents import plans, tick_gate

STEPS = [f"s{i}" for i in range(8)]


@pytest.fixture(params=["files", "db"])
def store(request, tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKY_STORE_BACKEND_PLANS", request.param)
    monkeypatch.setattr(tick_gate, "check_tick", lambda cid, node: None)
    monkeypatch.setattr(plans, "_push_assignment_invalidated", lambda *args: None)
    yield str(tmp_path)
    plans.drain_after_save()


def _tick_all_at_once(root: str, chat: str) -> None:
    start = threading.Barrier(len(STEPS))
    errors: list[BaseException] = []

    def tick(sid: str) -> None:
        start.wait()
        try:
            plans.update_node(chat, sid, status="completed", body_markdown=f"{sid} done", project_root=root)
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=tick, args=(sid,)) for sid in STEPS]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors, errors


_CHILD = """
import sys, time
from backend.agent.coding_agents import plans, tick_gate
tick_gate.check_tick = lambda cid, node: None
root, chat, sid, at = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
time.sleep(max(0.0, at - time.time()))
plans.update_node(chat, sid, status="completed", body_markdown=sid + " done", project_root=root)
plans.drain_after_save(60)  # 8 processes at once on a slow Mac runner took past the 10 s default
"""


@pytest.mark.parametrize("run", range(3))
def test_eight_seat_processes_tick_at_once(tmp_path, monkeypatch, run):
    # Each seat's Ducky tools run in their own process: an in-process lock can't keep them apart.
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path

    monkeypatch.setenv("DUCKY_STORE_BACKEND_PLANS", "files")
    root, chat = str(tmp_path), f"lead-p{run}"
    plans.create_plan(chat, nodes=[{"id": "go", "content": "Started"},
                                   *({"id": sid, "content": f"Step {sid}"} for sid in STEPS)], project_root=root)
    plans.update_node(chat, "go", status="in_progress", project_root=root)
    env = {**os.environ, "PYTHONPATH": str(Path(plans.__file__).parents[3])}
    at = time.time() + 8  # every child imports first, then all tick at the same moment
    children = [subprocess.Popen([sys.executable, "-c", _CHILD, root, chat, sid, str(at)], env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT) for sid in STEPS]
    for child in children:
        out, _ = child.communicate(timeout=120)
        assert child.returncode == 0, out.decode("utf-8", "replace")[-2000:]
    nodes = {n["id"]: n for n in plans._flatten_nodes(plans.load_plan(chat, root)["nodes"])}
    assert [(sid, nodes[sid]["status"]) for sid in STEPS] == [(sid, "completed") for sid in STEPS]


@pytest.mark.parametrize("run", range(20))
def test_eight_seats_tick_eight_steps_at_once(store, run):
    chat = f"lead-{run}"
    plans.create_plan(chat, nodes=[{"id": "go", "content": "Started"},
                                   *({"id": sid, "content": f"Step {sid}"} for sid in STEPS)],
                      project_root=store)
    plans.update_node(chat, "go", status="in_progress", project_root=store)  # the plan is playing
    _tick_all_at_once(store, chat)
    nodes = {n["id"]: n for n in plans._flatten_nodes(plans.load_plan(chat, store)["nodes"])}
    assert [(sid, nodes[sid]["status"], nodes[sid]["body_markdown"]) for sid in STEPS] == [
        (sid, "completed", f"{sid} done") for sid in STEPS]
