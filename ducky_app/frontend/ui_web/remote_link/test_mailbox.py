"""Mailbox jobs side by side, redelivered ids skipped, short backoff (remote_link.mailbox)."""

from __future__ import annotations

import threading

from frontend.ui_web.remote_link.mailbox import BACKOFF_MAX_S, BACKOFF_MIN_S, MailboxJobs, next_backoff


def test_backoff_doubles_and_caps() -> None:
    assert next_backoff(BACKOFF_MIN_S) == BACKOFF_MIN_S * 2
    assert next_backoff(9.0) == BACKOFF_MAX_S == 10.0
    assert next_backoff(0.0) == BACKOFF_MIN_S


def test_jobs_run_side_by_side_and_a_redelivered_id_is_skipped() -> None:
    pool = MailboxJobs(workers=2)
    gate = threading.Event()
    done: list[str] = []
    fast = threading.Event()
    assert pool.submit("a", lambda: gate.wait(5.0) and done.append("a"))
    assert pool.submit("a", lambda: done.append("dup")) is False
    assert pool.submit("b", lambda: (done.append("b"), fast.set()))
    assert fast.wait(1.0) and done == ["b"]
    gate.set()
    for _ in range(200):
        if pool.running() == 0:
            break
        threading.Event().wait(0.01)
    assert sorted(done) == ["a", "b"]
    # Finished: the same id may run again (the site sends it again only if no answer landed).
    again = threading.Event()
    assert pool.submit("a", again.set) and again.wait(1.0)
