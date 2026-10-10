"""On-demand, bounded CPU workers for the panel's large text comparisons.

Bridge processes never enable this service. Workers receive only text and return
counts; filesystem writes, journal state, MCP sessions and UI stay in the parent.
"""

from __future__ import annotations

import atexit
import multiprocessing
import os
import threading
import time
from concurrent.futures import CancelledError
from typing import Any

from backend.workspace.paths import line_delta as _local_delta

_MIN_COMBINED_CHARS = 256 * 1024
_JOB_TIMEOUT_SEC = 120.0
# Large diffs are rare: two worker processes kept for the rest of the session
# after the first one cost far more than starting them again.
_IDLE_CLOSE_SEC = 120.0


class DiffWorkers:
    def __init__(self, max_workers: int = 2) -> None:
        available = getattr(os, "process_cpu_count", os.cpu_count)() or 1
        self.max_workers = min(max(1, max_workers), 2, max(1, available - 1))
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(self.max_workers * 2)
        self._stopped = threading.Event()
        self._pool: Any = None
        self._active = 0
        self._idle_due = 0.0
        self._idle_armed = False

    def compare(self, before: str, after: str) -> tuple[int, int]:
        while not self._slots.acquire(timeout=0.1):
            if self._stopped.is_set():
                raise CancelledError("text comparison workers stopped")
        try:
            with self._lock:
                if self._stopped.is_set():
                    raise CancelledError("text comparison workers stopped")
                if self._pool is None:
                    # Pool exposes terminate/join on Python 3.13, allowing app
                    # exit to stop pure CPU work without private executor APIs.
                    self._pool = multiprocessing.get_context("spawn").Pool(self.max_workers)
                result = self._pool.apply_async(_local_delta, (before, after))
                self._active += 1
            try:
                deadline = time.monotonic() + _JOB_TIMEOUT_SEC
                while not self._stopped.is_set():
                    try:
                        return result.get(timeout=0.1)
                    except multiprocessing.TimeoutError:
                        if time.monotonic() >= deadline:
                            # A crashed/replaced Pool worker can leave an unresolved
                            # result. Bound the wait and retire the entire old pool.
                            self.close()
                            raise TimeoutError("large text comparison exceeded its time limit") from None
                raise CancelledError("text comparison workers stopped")
            finally:
                self._job_done()
        finally:
            self._slots.release()

    def _job_done(self) -> None:
        with self._lock:
            self._active -= 1
            self._idle_due = time.monotonic() + _IDLE_CLOSE_SEC
            if self._idle_armed or self._pool is None:
                return
            self._idle_armed = True
        self._arm_idle(_IDLE_CLOSE_SEC)

    def _arm_idle(self, delay: float) -> None:
        timer = threading.Timer(delay, self._close_if_idle)
        timer.daemon = True
        timer.start()

    def _close_if_idle(self) -> None:
        """Retire a pool with no work for _IDLE_CLOSE_SEC; the next large diff starts another."""
        with self._lock:
            pool = self._pool
            if pool is None or self._stopped.is_set():
                self._idle_armed = False
                return
            wait = self._idle_due - time.monotonic()
            if self._active or wait > 0.05:
                delay: float | None = wait if wait > 0.05 else _IDLE_CLOSE_SEC
            else:
                self._pool = None
                self._idle_armed = False
                delay = None
        if delay is not None:
            self._arm_idle(delay)
            return
        # Idle workers exit on their own (a frozen build's worker then removes its
        # unpacked files) rather than being terminated like at app exit.
        pool.close()
        pool.join()

    def close(self) -> None:
        with self._lock:
            self._stopped.set()
            pool, self._pool = self._pool, None
        if pool is not None:
            # All jobs are pure computations. Discard this pool's queues along
            # with it; they are never reused after terminating workers.
            pool.terminate()
            pool.join()


_workers: DiffWorkers | None = None
_service_lock = threading.Lock()


def enable() -> None:
    """Called by the panel only; creates no processes until a large diff arrives."""
    global _workers
    with _service_lock:
        if _workers is None:
            _workers = DiffWorkers()
            atexit.register(shutdown)


def shutdown() -> None:
    with _service_lock:
        workers = _workers
    if workers is not None:
        workers.close()


def line_delta(before: str, after: str) -> tuple[int, int]:
    workers = _workers
    if before == after:
        return 0, 0
    if workers is None or not before or not after or len(before) + len(after) < _MIN_COMBINED_CHARS:
        return _local_delta(before, after)
    return workers.compare(before, after)
