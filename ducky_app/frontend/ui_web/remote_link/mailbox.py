"""The site mailbox's jobs run side by side, and a failed wait never pauses long.

The PC reads phone and website commands from the site's mailbox one at a time,
but answering one can take a while: ``rtc_connect`` waits up to 20 s for the
page, and a chat load can take seconds while many agents run. Each job runs on
a small pool, so the next command is read and answered meanwhile. The site may
deliver an id again while it still runs; that copy is skipped (the first run
posts the answer).
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable

_log = logging.getLogger(__name__)

WORKERS = 4
BACKOFF_MIN_S = 2.0
BACKOFF_MAX_S = 10.0


def next_backoff(current: float) -> float:
    """Pause after another failed mailbox wait: doubles, never past 10 s."""
    return min(BACKOFF_MAX_S, max(BACKOFF_MIN_S, current * 2))


class MailboxJobs:
    def __init__(self, workers: int = WORKERS, name: str = "duckyos-rpc-job") -> None:
        self._q: queue.SimpleQueue[tuple[str, Callable[[], Any]]] = queue.SimpleQueue()
        self._workers = max(1, int(workers))
        self._name = name
        self._started = 0
        self._lock = threading.Lock()
        self._running: set[str] = set()

    def submit(self, job_id: str, fn: Callable[[], Any]) -> bool:
        """False when that id is already running (a redelivered copy)."""
        with self._lock:
            if job_id and job_id in self._running:
                return False
            if job_id:
                self._running.add(job_id)
            while self._started < self._workers:
                threading.Thread(target=self._work, name=f"{self._name}_{self._started}", daemon=True).start()
                self._started += 1
        self._q.put((job_id, fn))
        return True

    def running(self) -> int:
        with self._lock:
            return len(self._running)

    def _work(self) -> None:
        while True:
            job_id, fn = self._q.get()
            try:
                fn()
            except Exception:
                _log.exception("mailbox job %s failed", job_id)
            finally:
                with self._lock:
                    self._running.discard(job_id)


_jobs: MailboxJobs | None = None
_jobs_lock = threading.Lock()


def jobs() -> MailboxJobs:
    global _jobs
    with _jobs_lock:
        if _jobs is None:
            _jobs = MailboxJobs()
        return _jobs
