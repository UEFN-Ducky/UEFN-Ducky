"""App <-> host pipe: frames round-trip fast, and a dead or silent host is noticed within 1 s."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

from frontend.agent_host import protocol

_DUCKY_APP = str(Path(__file__).resolve().parents[2])
_CHILD = """
import sys, time
sys.path.insert(0, sys.argv[3])
from frontend.agent_host import protocol
protocol.connect_app(sys.argv[1], bytes.fromhex(sys.argv[2]), lambda frame: None)
time.sleep(60)
"""


def _pair(slot: str, *, host_frame, app_frame, app_closed=None, dead_after_s=protocol.DEAD_AFTER_S):
    key = os.urandom(16)
    listener = protocol.listen(slot, key)
    host: list[protocol.Channel] = []
    t = threading.Thread(
        target=lambda: host.append(protocol.connect_app(protocol.pipe_name(slot), key, host_frame)), daemon=True
    )
    t.start()
    app = protocol.accept_host(listener, app_frame, app_closed, dead_after_s=dead_after_s)
    t.join(5)
    listener.close()
    return app, host[0]


def test_10000_frames_round_trip_under_1s():
    got = []
    done = threading.Event()
    host_ref: list[protocol.Channel] = []

    def on_host(frame):
        host_ref[0].send("event", conv_id=frame["conv_id"], seq=frame["seq"])

    def on_app(frame):
        got.append(frame["seq"])
        if len(got) == 10_000:
            done.set()

    app, host = _pair(f"t-{uuid.uuid4().hex[:8]}", host_frame=on_host, app_frame=on_app)
    host_ref.append(host)
    t0 = time.perf_counter()
    for i in range(10_000):
        app.send("start_turn", conv_id="c1", text="hi", mode="agent", model="claude-haiku-5-5", seq=i)
    assert done.wait(5)
    assert time.perf_counter() - t0 < 1.0
    assert got == list(range(10_000))
    app.close()
    host.close()


def test_unknown_op_is_refused():
    app, host = _pair(f"t-{uuid.uuid4().hex[:8]}", host_frame=lambda f: None, app_frame=lambda f: None)
    with pytest.raises(ValueError):
        app.send("event")
    app.close()
    assert not app.send("cancel", conv_id="c1")
    host.close()


def test_silent_host_is_seen_by_missing_heartbeat():
    reasons: list[str] = []
    closed = threading.Event()
    app, host = _pair(
        f"t-{uuid.uuid4().hex[:8]}",
        host_frame=lambda f: None,
        app_frame=lambda f: None,
        app_closed=lambda r: (reasons.append(r), closed.set()),
        dead_after_s=0.5,
    )
    host._heartbeat_s = 0  # a hung host sends nothing
    assert closed.wait(1.0)
    assert reasons == ["heartbeat"]
    host.close()


def test_a_slow_handler_never_looks_like_a_missing_heartbeat():
    got: list[int] = []
    reasons: list[str] = []

    def slow(frame):
        if frame["seq"] == 0:
            time.sleep(1.2)  # a push stuck on disk, past dead_after_s
        got.append(frame["seq"])

    app, host = _pair(f"t-{uuid.uuid4().hex[:8]}", host_frame=lambda f: None, app_frame=slow,
                      app_closed=reasons.append, dead_after_s=0.6)
    host._heartbeat_s = 0.1
    for i in range(5):
        host.send("event", conv_id="c1", seq=i)
    deadline = time.monotonic() + 5
    while len(got) < 5 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert got == [0, 1, 2, 3, 4]
    assert reasons == [] and not app.closed
    app.close()
    host.close()


def test_killed_host_is_seen_within_1s():
    slot = f"t-{uuid.uuid4().hex[:8]}"
    key = os.urandom(16)
    listener = protocol.listen(slot, key)
    proc = subprocess.Popen([sys.executable, "-c", _CHILD, protocol.pipe_name(slot), key.hex(), _DUCKY_APP])
    reasons: list[str] = []
    closed = threading.Event()
    box: list[protocol.Channel] = []
    t = threading.Thread(
        target=lambda: box.append(
            protocol.accept_host(listener, lambda f: None, lambda r: (reasons.append(r), closed.set()))
        ),
        daemon=True,
    )
    t.start()
    t.join(30)
    try:
        assert box, "host never connected"
        proc.kill()
        t0 = time.perf_counter()
        assert closed.wait(1.0)
        assert time.perf_counter() - t0 < 1.0
        assert reasons == ["eof"]
    finally:
        proc.kill()
        proc.wait(5)
        listener.close()
