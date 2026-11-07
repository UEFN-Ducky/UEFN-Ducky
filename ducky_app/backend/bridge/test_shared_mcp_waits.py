"""Shared MCP daemon under long waits: quick reads still answer, stuck reads stop, waits send progress.

Nov 4 2026: 13 calls that take milliseconds (ducky_get_plan, workspace_read_file, …) hung until
Claude Code's 1800 s abort, and five ducky_ask_user calls died the same way while the person
was still deciding.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from backend.bridge import call_limits, shared_mcp
from backend.bridge.test_shared_mcp import FakeMcp, _hello, serve


class WaitingMcp(FakeMcp):
    """``ducky_ask_user`` waits on a thread the way the question gate does; a stuck
    ``ducky_get_plan`` does the same; everything else answers at once."""

    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()

    async def call_tool(self, name, arguments):
        if name in ("ducky_ask_user", "ducky_get_plan", "unreal__call_tool"):
            await asyncio.to_thread(self.release.wait)
            return [{"type": "text", "text": f"{name}: answered"}]
        return await super().call_tool(name, arguments)


@pytest.fixture
def daemon(monkeypatch, tmp_path):
    monkeypatch.delenv(shared_mcp.SLOT_ENV, raising=False)
    monkeypatch.setattr(shared_mcp, "_report_stuck", lambda *a, **k: None)
    mcp = WaitingMcp()
    thread = serve(mcp, monkeypatch, tmp_path)
    try:
        yield mcp
    finally:
        mcp.release.set()
        shared_mcp.request_stop()
        thread.join(timeout=6)


def _call(sock, req_id, name, token=None):
    params = {"name": name, "arguments": {}}
    if token is not None:
        params["_meta"] = {"progressToken": token}
    shared_mcp.write_frame(sock, {"op": "mcp", "id": req_id, "method": "tools/call", "params": params})


def _answer(sock, req_id, deadline_s=8.0):
    """The reply to ``req_id``; progress notices on the way are returned too."""
    sock.settimeout(deadline_s)
    notes = []
    while True:
        frame = shared_mcp.read_frame(sock)
        if frame.get("method") == "notifications/progress" and "id" not in frame:
            notes.append((time.monotonic(), frame["params"]))
            continue
        if frame.get("id") == req_id:
            return frame, notes


def _text(frame):
    return frame["result"]["content"][0]["text"]


def test_quick_reads_answer_while_six_clients_hold_more_waits_than_the_old_pool(daemon) -> None:
    """70 open waits from 6 clients: the old 64-thread pool was full and every later call
    queued at its first step. The other 2 clients' reads must still answer at once."""
    waiters = [_hello(f"waiter-{n}") for n in range(6)]
    readers = [_hello(f"reader-{n}") for n in range(2)]
    try:
        req = 0
        for n in range(70):
            req += 1
            _call(waiters[n % 6], req, "ducky_ask_user")
        time.sleep(0.5)  # every wait is open on its own thread
        for sock in readers:
            for read in range(5):
                started = time.monotonic()
                _call(sock, 1000 + read, "workspace_read_file")
                frame, _ = _answer(sock, 1000 + read, deadline_s=5.0)
                took = time.monotonic() - started
                assert _text(frame).startswith("workspace_read_file:"), frame
                assert took < 1.0, f"quick read took {took:.2f} s with 70 waits open"
        daemon.release.set()  # the person answers: every wait still gets its result
        for n, sock in enumerate(waiters):
            expected = set(range(n + 1, 71, 6))
            sock.settimeout(8.0)
            while expected:  # replies come back in any order
                frame = shared_mcp.read_frame(sock)
                if frame.get("id") in expected:
                    assert _text(frame) == "ducky_ask_user: answered"
                    expected.discard(frame["id"])
    finally:
        for sock in waiters + readers:
            shared_mcp.close_handle(sock)


def test_a_quick_read_stuck_past_its_deadline_returns_an_error(daemon, monkeypatch) -> None:
    monkeypatch.setattr(call_limits, "QUICK_READ_LIMIT_S", 0.3)
    sock = _hello("stuck-read")
    try:
        started = time.monotonic()
        _call(sock, 1, "ducky_get_plan")
        frame, _ = _answer(sock, 1, deadline_s=5.0)
        assert time.monotonic() - started < 3.0
        assert frame["result"]["isError"] is True
        assert "did not answer within" in _text(frame)
        _call(sock, 2, "workspace_read_file")  # the connection still works
        assert _text(_answer(sock, 2)[0]).startswith("workspace_read_file:")
    finally:
        shared_mcp.close_handle(sock)


def test_ask_user_sends_progress_through_a_long_wait_and_still_gets_the_answer(daemon, monkeypatch) -> None:
    """35 progress intervals (one simulated minute each) of the person thinking: a notice
    arrives every interval and the answer still comes back to the same call."""
    every = 0.05
    monkeypatch.setattr(shared_mcp, "PROGRESS_EVERY_S", every)
    sock = _hello("asker")
    try:
        started = time.monotonic()
        _call(sock, 7, "ducky_ask_user", token="tok-7")
        threading.Timer(35 * every, daemon.release.set).start()
        frame, notes = _answer(sock, 7, deadline_s=10.0)
        assert _text(frame) == "ducky_ask_user: answered"
        assert len(notes) >= 25, f"only {len(notes)} progress notices in 35 intervals"
        assert all(p["progressToken"] == "tok-7" for _, p in notes)
        assert [p["progress"] for _, p in notes] == list(range(1, len(notes) + 1))
        stamps = [started] + [t for t, _ in notes]
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        assert max(gaps) < every * 6, f"longest gap {max(gaps):.2f} s"
    finally:
        shared_mcp.close_handle(sock)


def test_no_progress_without_a_token_and_none_after_the_answer(daemon, monkeypatch) -> None:
    monkeypatch.setattr(shared_mcp, "PROGRESS_EVERY_S", 0.05)
    sock = _hello("quiet")
    try:
        _call(sock, 1, "ducky_ask_user")  # no progressToken: the client didn't ask for any
        threading.Timer(0.4, daemon.release.set).start()
        frame, notes = _answer(sock, 1)
        assert _text(frame) == "ducky_ask_user: answered" and notes == []
        _call(sock, 2, "workspace_read_file", token="tok-2")
        _answer(sock, 2)
        time.sleep(0.3)
        sock.settimeout(0.2)
        with pytest.raises((TimeoutError, OSError)):
            shared_mcp.read_frame(sock)  # nothing more arrives for a finished call
    finally:
        shared_mcp.close_handle(sock)


def test_the_spawn_executor_runs_each_job_on_its_own_thread_up_to_its_cap() -> None:
    pool = shared_mcp._SpawnExecutor(3, "t")
    gate = threading.Event()
    held = [pool.submit(gate.wait) for _ in range(3)]
    queued = pool.submit(lambda: "ran")
    time.sleep(0.2)
    assert not queued.done()  # bounded: the fourth waits for a slot
    gate.set()
    assert queued.result(timeout=3) == "ran"
    assert all(f.result(timeout=3) is True for f in held)
    pool.shutdown(wait=True)
    assert pool.running() == 0
    with pytest.raises(RuntimeError):
        pool.submit(lambda: None)
