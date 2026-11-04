"""The UEFN listener status, answered from a cache so the window never waits on UEFN.

``get_listener_status`` is the window's most frequent call. It used to fetch inline,
which takes seconds when UEFN is busy and stalls the window. Now every caller gets
the last value at once; when that value is older than ``max_age_s``, one background
fetch refreshes it (many callers at once still start one fetch). Nobody asking means
no fetches, so a busy UEFN is never pinged more than before.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

MAX_AGE_SEC = 2.0


class ListenerStatusCache:
    def __init__(
        self,
        fetch: Callable[[], dict[str, Any]],
        placeholder: Callable[[], dict[str, Any]],
        *,
        max_age_s: float = MAX_AGE_SEC,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch
        self._placeholder = placeholder
        self.max_age_s = float(max_age_s)
        self._clock = clock
        self._lock = threading.Lock()
        self._value: dict[str, Any] | None = None
        self._fetched_at: float | None = None
        self._inflight = False
        self._again = False  # refresh() while a fetch ran: that fetch may predate the change
        self.fetches = 0

    @property
    def value(self) -> dict[str, Any] | None:
        return self._value

    def get(self) -> dict[str, Any]:
        """The last status (a copy), or the placeholder before the first fetch ends. Never blocks."""
        with self._lock:
            value = self._value
            fresh = self._fetched_at is not None and self._clock() - self._fetched_at < self.max_age_s
            self._start_locked(force=not fresh)
        return dict(value) if value is not None else self._placeholder()

    def refresh(self) -> None:
        """Fetch again now (a restart, a project switch), without blocking the caller."""
        with self._lock:
            if self._inflight:
                self._again = True
            self._start_locked(force=True)

    def _start_locked(self, *, force: bool) -> None:
        if not force or self._inflight:
            return
        self._inflight = True
        threading.Thread(target=self._run_fetch, daemon=True, name="listener-status").start()

    def _run_fetch(self) -> None:
        while True:
            try:
                result = self._fetch()
            except Exception as exc:  # noqa: BLE001 — one bad fetch must not wedge the cache
                result = dict(self._placeholder(), status_text="Offline", error=str(exc))
            with self._lock:
                self._value = result
                self._fetched_at = self._clock()
                self.fetches += 1
                if not self._again:
                    self._inflight = False
                    return
                self._again = False
