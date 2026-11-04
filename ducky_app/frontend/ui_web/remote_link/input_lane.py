"""Remote input and control frames on their own small, high-priority workers.

Pointer/key frames and the direct-link handshake are tiny and must never wait
behind chat loads, file reads or list calls (which share the normal pools and
fill up while hundreds of agents run). These methods get two workers of their own.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable

_log = logging.getLogger(__name__)

INPUT_METHODS = frozenset(
    {
        "window_input",
        "window_box",
        # The answer back from the page; the offer (direct_rtc_connect) waits up
        # to 20 s for it, so it stays on the normal pool.
        "direct_rtc_answer",
        "rtc_report",
    }
)
WORKERS = 2


def is_input_method(method: str) -> bool:
    return str(method or "").strip().strip("/") in INPUT_METHODS


class InputLane:
    def __init__(self, workers: int = WORKERS, name: str = "remote-input") -> None:
        self._q: queue.SimpleQueue[tuple[Callable[..., Any], tuple[Any, ...]]] = queue.SimpleQueue()
        self._name = name
        self._workers = max(1, int(workers))
        self._started = 0
        self._lock = threading.Lock()

    def submit(self, fn: Callable[..., Any], *args: Any) -> None:
        with self._lock:
            while self._started < self._workers:
                threading.Thread(target=self._work, name=f"{self._name}_{self._started}", daemon=True).start()
                self._started += 1
        self._q.put((fn, args))

    def thread_count(self) -> int:
        return self._started

    def _work(self) -> None:
        while True:
            fn, args = self._q.get()
            try:
                fn(*args)
            except Exception:
                _log.exception("%s task failed", self._name)


_lane: InputLane | None = None
_lane_lock = threading.Lock()


def input_lane() -> InputLane:
    global _lane
    with _lane_lock:
        if _lane is None:
            _lane = InputLane()
        return _lane
