"""New agent turns wait for memory: never below the free-RAM floor, never pausing running ones."""

from __future__ import annotations

import threading
from collections import deque

import pytest

from backend.util import proc_table
from frontend.ui_web import admission as adm

GB = 1024**3


@pytest.fixture
def pc(monkeypatch):
    """A fake PC: free RAM drops by each started agent's cost, until its turn ends."""
    state = {"base": 20.0, "cost": 0.23, "started": [], "t": 1000.0}
    lock = threading.Lock()
    monkeypatch.setattr(adm, "_waiting", [])
    monkeypatch.setattr(adm, "_running", set())
    monkeypatch.setattr(adm, "_admits", deque())
    monkeypatch.setattr(adm, "_cost", {"gb": 0.0, "at": float("-inf")})
    monkeypatch.setattr(adm, "_role", lambda cid: "member")
    monkeypatch.setattr(adm, "_ensure_thread", lambda: None)
    monkeypatch.setattr(adm, "_now", lambda: state["t"])
    monkeypatch.setattr(adm, "min_free_gb", lambda: 6.0)
    monkeypatch.setattr(adm, "_read_free", lambda: state["base"] - state["cost"] * len(state["started"]))

    def table():
        # What the live agents measure: one claude.exe plus a node and a bridge each.
        n = len(state["started"])
        parts = [("claude.exe", 0.17), ("node.exe", 0.03), ("UEFN-Ducky-Bridge.exe", 0.03)]
        rows = {}
        for i in range(n):
            for j, (name, gb) in enumerate(parts):
                pid = i * 10 + j + 1
                rows[pid] = proc_table.Proc(pid, 0, name, 0, int(gb * GB), 1, 1, 0.0, 0.0)
        return rows

    monkeypatch.setattr(proc_table, "table", table)

    def ask(cid):
        def go():
            with lock:
                state["started"].append(cid)
        ok = adm.may_start(cid, "agent", start=go)
        if ok:
            go()
        return ok

    def wait_started(n, timeout=5.0):
        import time

        deadline = time.monotonic() + timeout
        while len(state["started"]) < n and time.monotonic() < deadline:
            time.sleep(0.01)
        return len(state["started"])

    state["ask"], state["wait"] = ask, wait_started
    return state


def test_free_20_gb_starts(pc):
    pc["base"] = 20.0
    assert pc["ask"]("a") and pc["ask"]("b") and pc["ask"]("c")
    assert adm._waiting == []


def test_free_5_gb_queues_past_the_floor_of_two(pc):
    pc["base"] = 5.0 + 2 * pc["cost"]  # 5 GB free once the two floor turns run
    assert pc["ask"]("a") and pc["ask"]("b")  # a team always moves
    pushed = []
    assert not adm.may_start("c", "agent", start=lambda: None, push=pushed.append)
    assert [w.conv_id for w in adm._waiting] == ["c"]
    assert pushed[-1]["text"] == "Waiting for memory (5.0 GB free) — 1st in line"


def test_a_queued_turn_starts_when_free_ram_returns(pc):
    pc["base"] = 7.0
    for cid in ("a", "b"):
        assert pc["ask"](cid)
    assert not pc["ask"]("c")
    pc["base"] = 30.0  # another app closed
    assert adm.drain() == 1
    assert pc["wait"](3) == 3 and adm.holds("c")


def test_running_agents_are_never_stopped_only_new_starts_wait(pc):
    pc["base"] = 7.0
    assert pc["ask"]("a") and pc["ask"]("b")
    pc["base"] = 1.0  # the PC got tight
    assert not pc["ask"]("c")
    assert adm.holds("a") and adm.holds("b")  # still running


def test_the_cost_is_measured_from_the_live_agents(pc):
    pc["started"].extend(["x"] * 4)
    pc["t"] += adm.COST_EVERY_S
    assert adm.agent_cost_gb() == pytest.approx(0.23, abs=0.01)


def test_300_requested_at_25_gb_start_in_waves_and_never_cross_the_floor(pc):
    pc["base"] = 25.0
    lowest = pc["base"]
    started_now = sum(pc["ask"](f"s{i}") for i in range(300))
    waves = [started_now]
    lowest = min(lowest, adm._read_free())
    assert 0 < started_now < 300
    while adm._waiting:
        pc["t"] += adm.RESERVE_S + 1  # the started agents have loaded
        before = len(pc["started"])
        adm.drain()
        pc["wait"](before)
        lowest = min(lowest, adm._read_free())
        if len(pc["started"]) == before:
            # The PC is full: seats finish, freeing their memory.
            for cid in list(pc["started"])[:20]:
                pc["started"].remove(cid)
                adm.release(cid)
            pc["wait"](before - 20)
            lowest = min(lowest, adm._read_free())
            continue
        waves.append(len(pc["started"]) - before)
    assert lowest >= 6.0 - pc["cost"]  # the floor holds (within one agent's estimate)
    assert len(waves) > 1  # in waves, not all at once
    assert adm._waiting == []  # the line drained as seats finished
