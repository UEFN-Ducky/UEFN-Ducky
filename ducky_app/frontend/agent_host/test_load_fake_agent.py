"""Load test with fake agents: real hosts (``main.serve``, Batcher, Channel), no CLI, no LLM.

``STUB_HOST`` replaces the host's ``turns`` with a streamer: each ``start_turn`` sends
``deltas`` text deltas (a tool start/done every 5) with random 50-500 ms sleeps, then
``turn_ended`` with how many it sent. ``run_fake_agents`` drives N of them through
``Supervisor.assign``; the final load test reuses both. ``LOCALAPPDATA`` points at tmp_path,
so no real chat database or log folder is touched.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any

import psutil

from frontend.agent_host import supervisor

_DUCKY_APP = str(Path(__file__).resolve().parents[2])
STUB_HOST = r"""
import os, random, sys, threading, time, types
sys.path.insert(0, sys.argv[1])
from frontend.agent_host import main as host_main

def on_frame(channel, frame):
    if frame.get("op") != "start_turn":
        return
    conv, run, deltas = frame["conv_id"], frame["run_id"], int(frame.get("text") or 10)
    rng = random.Random(run)
    def go():
        for n in range(1, deltas + 1):
            host_main.feed({"conv_id": conv, "run_id": run, "type": "text_delta", "text": "tok "})
            if n % 5 == 0:
                host_main.feed({"conv_id": conv, "run_id": run, "type": "tool", "tool": {"name": "Read"}})
                host_main.feed({"conv_id": conv, "run_id": run, "type": "tool_done", "tool": {"name": "Read"}})
            time.sleep(rng.uniform(0.05, 0.5))
        host_main.feed({"conv_id": conv, "run_id": run, "type": "agent_stopped"})
        host_main._batcher.flush()
        channel.send("turn_ended", conv_id=conv, run_id=run, error="", sent=deltas)
    threading.Thread(target=go, daemon=True).start()

sys.modules["frontend.agent_host.turns"] = types.SimpleNamespace(on_frame=on_frame)
host_main.serve(sys.argv[2], bytes.fromhex(os.environ.pop("UEFN_DUCKY_HOST_KEY")))
"""


def stub_argv(script: Path):
    """``Supervisor(argv_for=...)``: start ``STUB_HOST`` (written to ``script``) instead of a real host."""
    script.write_text(STUB_HOST, encoding="utf-8")
    return lambda slot, pipe: [sys.executable, str(script), _DUCKY_APP, pipe]


class Tally:
    """``Supervisor(on_frame=...)``: text tokens and ended turns per chat."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.tokens: dict[str, int] = {}
        self.sent: dict[str, int] = {}
        self.frames = 0

    def __call__(self, host: Any, frame: dict[str, Any]) -> None:
        with self.lock:
            conv = str(frame.get("conv_id") or "")
            if frame["op"] == "event":
                self.frames += 1
                for ev in frame.get("events") or []:
                    if ev.get("type") == "text_delta":
                        self.tokens[conv] = self.tokens.get(conv, 0) + str(ev.get("text") or "").count("tok ")
            elif frame["op"] == "turn_ended":
                self.sent[conv] = int(frame.get("sent") or 0)


def run_fake_agents(sup: supervisor.Supervisor, tally: Tally, agents: int, *, deltas: int = 10,
                    timeout_s: float = 90.0) -> list[supervisor.Host]:
    """Seat ``agents`` chats, start one fake turn each, wait for every ``turn_ended``. Returns the seats."""
    seats = [sup.assign(f"c{i}") for i in range(agents)]
    for host in {h.slot: h for h in seats}.values():
        assert host.ready.wait(60), f"host {host.slot} never connected"
    for i, host in enumerate(seats):
        assert host.send("start_turn", conv_id=f"c{i}", run_id=f"r{i}", text=str(deltas), model="", mode="agent")
    deadline = time.monotonic() + timeout_s
    while len(tally.sent) < agents and time.monotonic() < deadline:
        time.sleep(0.1)
    return seats


def test_fifty_fake_agents_finish_and_idle_hosts_exit(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    tally = Tally()
    sup = supervisor.Supervisor(on_frame=tally, argv_for=stub_argv(tmp_path / "stub_host.py"),
                                log_dir=tmp_path / "logs", per_host=4, max_hosts=16, idle_exit_s=0.5, reap_s=0.1)
    try:
        seats = run_fake_agents(sup, tally, 50)
        hosts = {h.slot: h for h in seats}
        assert len(hosts) == 13
        assert len(tally.sent) == 50, sorted(set(f"c{i}" for i in range(50)) - set(tally.sent))
        assert tally.tokens == tally.sent  # every delta arrived, batched or not
        pids = [h.pid for h in hosts.values()]
        for i in range(50):
            sup.release(f"c{i}")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and (sup.hosts() or any(psutil.pid_exists(p) for p in pids)):
            time.sleep(0.1)
        assert sup.hosts() == []
        assert not any(psutil.pid_exists(p) for p in pids)
    finally:
        sup.stop_all()


def test_two_hundred_fake_agents_share_eight_hosts_evenly(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    tally = Tally()
    sup = supervisor.Supervisor(on_frame=tally, argv_for=stub_argv(tmp_path / "stub_host.py"),
                                log_dir=tmp_path / "logs", per_host=16, max_hosts=8)
    try:
        run_fake_agents(sup, tally, 200, deltas=5)
        counts = sorted(r["agents"] for r in sup.snapshot())
        assert len(counts) == 8 and sum(counts) == 200
        assert counts[-1] - counts[0] <= 1, counts  # past 8 x 16 the least-busy host takes the next
        assert len(tally.sent) == 200 and tally.tokens == tally.sent
    finally:
        sup.stop_all()
