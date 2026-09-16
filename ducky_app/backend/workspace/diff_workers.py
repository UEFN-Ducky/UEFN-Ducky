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


class DiffWorkers:
    def __init__(self, max_workers: int = 2) -> None:
        available = getattr(os, "process_cpu_count", os.cpu_count)() or 1
        self.max_workers = min(max(1, max_workers), 2, max(1, available - 1))
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(self.max_workers * 2)
        self._stopped = threading.Event()
        self._pool: Any = None

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
            self._slots.release()

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
