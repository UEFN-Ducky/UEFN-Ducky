"""Bridge jobs push bridge_job_progress (job id, state, percent), at most 2 a second per job."""

from __future__ import annotations

import threading
import time

from frontend.ui_web import bridge_jobs as bj


def _events(monkeypatch) -> list[dict]:
    events: list[dict] = []
    monkeypatch.setattr(bj, "_emit", events.append)
    return events


def _running(events: list[dict], jid: str) -> list[int]:
    return [e["percent"] for e in events if e["job_id"] == jid and e["state"] == "running"]


def _wait_state(events: list[dict], jid: str, state: str) -> None:
    deadline = time.monotonic() + 10.0  # a loaded runner
    while time.monotonic() < deadline:
        if any(e["job_id"] == jid and e["state"] == state for e in events):
            return
        time.sleep(0.01)
    raise AssertionError(f"no {state} event: {events}")


def test_progress_0_50_100_pushes_three_events(monkeypatch) -> None:
    events = _events(monkeypatch)
    monkeypatch.setattr(bj, "_PUSH_GAP_S", 0.01)

    def work() -> dict:
        for pct in (0, 50, 100):
            bj.job_progress(pct)
            time.sleep(0.05)
        return {"ok": True}

    jid = bj.job_start(work)["job_id"]
    _wait_state(events, jid, "done")
    assert _running(events, jid) == [0, 50, 100]
    assert all(e["type"] == "bridge_job_progress" for e in events)
    assert bj.job_poll(jid)["ok"] is True  # old pages still poll


def test_hundred_updates_in_a_second_push_at_most_two(monkeypatch) -> None:
    events = _events(monkeypatch)
    release = threading.Event()

    def work() -> dict:
        # No wall-clock check here: on a loaded runner the loop outlasts the second and the
        # job failed; the bound below is counted in a fixed 0.95 s whatever the loop's pace.
        for pct in range(100):
            bj.job_progress(pct)
            time.sleep(0.009)
        release.wait(10.0)
        return {"ok": True}

    jid = bj.job_start(work)["job_id"]
    time.sleep(0.95)
    assert len([e for e in events if e["job_id"] == jid]) <= 2
    release.set()
    _wait_state(events, jid, "done")
    assert events[-1]["percent"] == 100  # the last value always arrives
    bj.job_poll(jid)


def test_a_failed_job_pushes_error_and_a_cancelled_one_cancelled(monkeypatch) -> None:
    events = _events(monkeypatch)

    def boom() -> dict:
        raise RuntimeError("nope")

    failed = bj.job_start(boom)["job_id"]
    _wait_state(events, failed, "error")
    bj.job_poll(failed)

    slow = bj.job_start(lambda: (time.sleep(0.3), {"ok": True})[1])["job_id"]
    bj.job_cancel(slow)
    _wait_state(events, slow, "cancelled")
    time.sleep(0.4)
    assert [e["state"] for e in events if e["job_id"] == slow] == ["cancelled"]
