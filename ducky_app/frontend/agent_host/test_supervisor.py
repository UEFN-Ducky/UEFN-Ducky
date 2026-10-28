"""Supervisor: one host per ``per_host`` agents, hosts die with the app, idle hosts exit."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import psutil
import pytest

from frontend.agent_host import main as host_main
from frontend.agent_host import protocol, supervisor

_DUCKY_APP = str(Path(__file__).resolve().parents[2])
_FAKE_APP = """
import sys, time
sys.path.insert(0, sys.argv[1])
from frontend.agent_host import supervisor
sup = supervisor.Supervisor(log_dir=__import__("pathlib").Path(sys.argv[2]), per_host=4)
for i in range(6):
    sup.assign(f"c{i}")
for h in sup.hosts():
    assert h.ready.wait(60), "host never connected"
print(" ".join(str(h.pid) for h in sup.hosts()), flush=True)
time.sleep(120)
"""


def _gone(pids: list[int], within_s: float) -> bool:
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        if not any(psutil.pid_exists(p) for p in pids):
            return True
        time.sleep(0.1)
    return False


def test_six_agents_get_two_hosts(tmp_path):
    sup = supervisor.Supervisor(log_dir=tmp_path, per_host=4)
    try:
        seats = [sup.assign(f"c{i}") for i in range(6)]
        hosts = sup.hosts()
        assert len(hosts) == 2
        assert sorted(len(h.agents) for h in hosts) == [2, 4]
        assert sup.assign("c0") is seats[0]  # a chat keeps its host
        for h in hosts:
            assert h.ready.wait(60), (tmp_path / f"host-{h.slot}.log").read_text(errors="replace")
        assert [r["agents"] for r in sorted(sup.snapshot(), key=lambda r: r["slot"])] == [len(h.agents) for h in
                                                                                           sorted(hosts, key=lambda h: h.slot)]
    finally:
        sup.stop_all()


@pytest.mark.skipif(sys.platform != "win32", reason="job objects are Windows-only")
def test_killing_the_app_kills_its_hosts(tmp_path):
    app = subprocess.Popen([sys.executable, "-c", _FAKE_APP, _DUCKY_APP, str(tmp_path)], stdout=subprocess.PIPE,
                           text=True)
    try:
        line = app.stdout.readline()  # type: ignore[union-attr]
        pids = [int(p) for p in line.split()]
        assert len(pids) == 2, line
        assert all(psutil.pid_exists(p) for p in pids)
        app.kill()
        assert _gone(pids, 10.0)
    finally:
        app.kill()
        app.wait(10)


class _Recorder:
    closed = False

    def __init__(self):
        self.sent = []

    def send(self, op, **fields):
        self.sent.append((op, fields))
        return True


class _Proc:
    pid = 0

    def alive(self):
        return True


def test_set_visible_tells_each_host_only_its_chats(tmp_path):
    sup = supervisor.Supervisor(log_dir=tmp_path, reap_s=3600)
    a, b = (supervisor.Host(i, f"p{i}", b"k", _Proc()) for i in range(2))
    for host, convs in ((a, {"c1", "c2"}), (b, {"c3"})):
        host.channel = _Recorder()
        host.agents = set(convs)
        sup._hosts[host.slot] = host
    sup.set_visible(["c2", "c3", "elsewhere"])
    assert a.channel.sent == [("visible_chats", {"conv_ids": ["c2"]})]
    assert b.channel.sent == [("visible_chats", {"conv_ids": ["c3"]})]
    sup.assign("c4")  # joins a host with a free seat and that host hears its list again
    assert any(h.channel.sent[-1] == ("visible_chats", {"conv_ids": ["c2"] if h is a else ["c3"]}) for h in (a, b))
    sup._stopped.set()


def test_sixteen_chats_one_visible_stay_under_26_events_a_second():
    tag = f"t-{uuid.uuid4().hex[:8]}"
    key = os.urandom(16)
    listener = protocol.listen(tag, key)
    frames: list[tuple[float, dict]] = []
    done = threading.Event()
    host = threading.Thread(target=host_main.serve, args=(protocol.pipe_name(tag), key, done), daemon=True)
    host.start()
    conn = listener.accept()
    listener.close()
    app = protocol.Channel(conn, send_ops=supervisor.APP_OPS, recv_ops=protocol.HOST_OPS,
                           on_frame=lambda f: frames.append((time.monotonic(), f))).start()
    try:
        deadline = time.monotonic() + 5
        while host_main._batcher is None and time.monotonic() < deadline:
            time.sleep(0.01)
        app.send("visible_chats", conv_ids=["c0"])
        time.sleep(0.2)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 3.0:
            for i in range(16):
                host_main.feed({"conv_id": f"c{i}", "run_id": f"r{i}", "type": "text_delta", "text": "tok "})
            time.sleep(0.002)
        time.sleep(0.2)
        window = [f for ts, f in frames if t0 + 0.5 <= ts < t0 + 2.5 and f["op"] == "event"]
        assert len(window) / 2.0 <= 26, len(window)
        texts = {f["conv_id"] for f in window if any(e["type"] == "text_delta" for e in f["events"])}
        assert texts == {"c0"}  # hidden chats only get status lines

        app.send("visible_chats", conv_ids=["c0", "c5"])  # the person opened c5
        time.sleep(0.2)
        mark = len(frames)
        host_main.feed({"conv_id": "c5", "run_id": "r5", "type": "text_delta", "text": "now live"})
        time.sleep(0.3)
        assert any(f["conv_id"] == "c5" and e["type"] == "text_delta" and e["text"] == "now live"
                   for _, f in frames[mark:] if f["op"] == "event" for e in f["events"])
    finally:
        done.set()
        host.join(5)
        app.close()


def test_a_host_crash_only_stops_its_own_chats(tmp_path, monkeypatch):
    from frontend.agent_host import turns

    marked: list[int] = []
    monkeypatch.setattr(turns, "mark_host_dead", marked.append, raising=False)
    closed: list[str] = []
    sup = supervisor.Supervisor(log_dir=tmp_path, on_host_closed=lambda h, r: closed.append(r), per_host=4)
    try:
        for i in range(6):
            sup.assign(f"c{i}")
        a, b = sorted(sup.hosts(), key=lambda h: h.slot)
        assert a.ready.wait(60) and b.ready.wait(60)
        b_pid, b_chats = b.pid, set(b.agents)
        a.launched.proc.kill()  # the crash, mid-turn

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not (marked and len(sup.hosts()) == 2):
            time.sleep(0.05)
        assert marked == [a.slot]  # once, though EOF and the reaper may both see it
        assert len(closed) == 1
        fresh = next(h for h in sup.hosts() if h is not b)
        assert fresh is not a and fresh.ready.wait(60)
        assert b.alive() and b.pid == b_pid and b.agents == b_chats  # the other host never noticed
        assert all(sup.host_for(c) is b for c in b_chats)
        assert sup.assign("c0") is fresh  # the dead host's chats land on the fresh host
        time.sleep(0.3)
        assert marked == [a.slot]
    finally:
        sup.stop_all()


def test_defaults_seat_sixteen_per_host_and_env_overrides(monkeypatch):
    monkeypatch.delenv("DUCKY_AGENTS_PER_HOST", raising=False)
    assert supervisor._env_int("DUCKY_AGENTS_PER_HOST", 16) == 16
    assert 1 <= supervisor.MAX_HOSTS <= 8
    monkeypatch.setenv("DUCKY_AGENTS_PER_HOST", "5")
    assert supervisor._env_int("DUCKY_AGENTS_PER_HOST", 16) == 5
    monkeypatch.setenv("DUCKY_AGENTS_PER_HOST", "junk")
    assert supervisor._env_int("DUCKY_AGENTS_PER_HOST", 16) == 16


def test_idle_host_exits_after_timeout(tmp_path):
    sup = supervisor.Supervisor(log_dir=tmp_path, idle_exit_s=0.5, reap_s=0.1)
    try:
        host = sup.assign("c1")
        assert host.ready.wait(60)
        sup.reap()
        assert host.alive(), "a host with an agent never idles out"
        sup.release("c1")
        assert _gone([host.pid], 10.0)
        assert sup.hosts() == []
        assert sup.assign("c2") is not host  # the next turn starts a fresh host
    finally:
        sup.stop_all()
