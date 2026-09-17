from concurrent.futures import Future
from types import SimpleNamespace
import threading

import pytest

from frontend.ui_web import bridge_jobs as jobs


@pytest.fixture
def submitted(monkeypatch):
    futures = []

    class QueuedFuture(Future):
        def __init__(self, fn):
            super().__init__()
            self.fn = fn

        def set_result(self, result):
            self.fn()
            super().set_result(result)

    def submit(fn):
        future = QueuedFuture(fn)
        futures.append(future)
        return future

    monkeypatch.setattr(jobs, "_EXECUTOR", SimpleNamespace(submit=submit))
    monkeypatch.setattr(jobs, "_JOBS", {})
    monkeypatch.setattr(jobs, "_JOB_CANCEL", {})
    monkeypatch.setattr(jobs, "_JOB_COMPLETED_AT", {})
    monkeypatch.setattr(jobs, "_CANCELLED", {})
    monkeypatch.setattr(jobs, "_SLOTS", threading.BoundedSemaphore(2))
    monkeypatch.setattr(jobs, "_reaper_started", True)
    return futures


def test_grace_starts_at_completion_not_submission(submitted):
    jid = jobs.job_start(lambda: None)["job_id"]
    jobs._reap_expired(jobs.time.monotonic() + 1000)
    assert jobs.job_poll(jid)["pending"]
    submitted[0].set_result({"ok": True, "value": 42})
    completed = jobs._JOB_COMPLETED_AT[jid]
    jobs._reap_expired(completed + jobs._STALE_GRACE_S - 1)
    assert jobs.job_poll(jid)["value"] == 42
    assert not jobs._JOB_COMPLETED_AT


def test_reaper_releases_abandoned_result_without_another_start(submitted, monkeypatch):
    jid = jobs.job_start(lambda: None)["job_id"]
    submitted[0].set_result("large result")
    jobs._JOB_COMPLETED_AT[jid] -= jobs._STALE_GRACE_S + 1
    stops = iter([False, True])
    monkeypatch.setattr(jobs, "_REAPER_STOP", SimpleNamespace(wait=lambda _: next(stops)))
    jobs._reaper()
    assert not jobs._JOBS
    assert not jobs._JOB_COMPLETED_AT
    assert not jobs.job_poll(jid)["ok"]


def test_running_cancel_does_not_release_executor_capacity_early(submitted):
    first = jobs.job_start(lambda: None)["job_id"]
    submitted[0].set_running_or_notify_cancel()
    jobs.job_cancel(first)
    assert first not in jobs._JOBS
    jobs.job_start(lambda: None)
    assert not jobs.job_start(lambda: None)["ok"]
    submitted[0].set_result("discarded result")
    assert first not in jobs._JOB_COMPLETED_AT
    assert jobs.job_start(lambda: None)["ok"]
    assert jobs.job_poll(first)["cancelled"]


def test_unknown_cancels_do_not_accumulate_and_tombstones_expire(submitted):
    for n in range(200):
        jobs.job_cancel(str(n))
    assert not jobs._CANCELLED
    jid = jobs.job_start(lambda: None)["job_id"]
    jobs.job_cancel(jid)
    jobs._reap_expired(jobs._CANCELLED[jid] + jobs._STALE_GRACE_S)
    assert not jobs._CANCELLED


def test_result_capacity_rejects_instead_of_evicting_unpolled_results(submitted, monkeypatch):
    monkeypatch.setattr(jobs, "_MAX_JOBS", 1)
    jid = jobs.job_start(lambda: None)["job_id"]
    submitted[0].set_result(42)
    assert not jobs.job_start(lambda: None)["ok"]
    assert jobs.job_poll(jid)["result"] == 42
    assert jobs.job_start(lambda: None)["ok"]


def test_immediate_completion_callback_does_not_deadlock(submitted, monkeypatch):
    def submit(fn):
        future = Future()
        future.set_result(fn())
        return future

    monkeypatch.setattr(jobs, "_EXECUTOR", SimpleNamespace(submit=submit))
    started = jobs.job_start(lambda: "instant")
    assert jobs.job_poll(started["job_id"])["result"] == "instant"


def test_failed_submission_returns_capacity(submitted, monkeypatch):
    def fail(_fn):
        raise RuntimeError("executor stopped")

    monkeypatch.setattr(jobs, "_EXECUTOR", SimpleNamespace(submit=fail))
    for _ in range(3):
        with pytest.raises(RuntimeError, match="executor stopped"):
            jobs.job_start(lambda: None)
    assert not jobs._JOBS


def test_repeated_queued_cancellation_cannot_grow_executor_backlog(submitted):
    ran = []
    for _ in range(2):
        jid = jobs.job_start(lambda: ran.append(True))["job_id"]
        jobs.job_cancel(jid)
    assert not jobs.job_start(lambda: None)["ok"]
    # A worker must dequeue the cancelled wrapper before its slot is reusable.
    submitted[0].set_result(None)
    assert not ran
    assert jobs.job_start(lambda: None)["ok"]
