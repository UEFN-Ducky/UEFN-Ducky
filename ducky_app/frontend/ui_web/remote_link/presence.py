"""Presence beats that never let the site's online window run out.

The site counts a PC online for ``live_s`` seconds after its last report
(330 s today). A beat every ``live_s / 3`` means two beats can fail in a row
and the PC still shows online; a failed beat retries after a short pause
instead of waiting a whole interval. The beat runs on its own daemon thread,
so agent work, UEFN calls and the mailbox never delay it.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

MISSED_BEATS_OFFLINE = 3
DEFAULT_LIVE_S = 90.0
MIN_INTERVAL_S = 5.0
MAX_INTERVAL_S = 100.0
RETRY_S = (2.0, 5.0, 10.0)


def beat_interval(live_s: float | None) -> float:
    """Seconds between beats: under a third of the online window."""
    window = float(live_s) if live_s and live_s > 0 else DEFAULT_LIVE_S
    return max(MIN_INTERVAL_S, min(MAX_INTERVAL_S, window / MISSED_BEATS_OFFLINE * 0.9))


def is_online(last_seen: float, now: float, interval_s: float) -> bool:
    """A viewer says "not available" only after 3 missed beats."""
    return last_seen > 0 and now - last_seen <= interval_s * MISSED_BEATS_OFFLINE


def retry_delay(failures: int, interval_s: float) -> float:
    """Pause after ``failures`` failed beats in a row: short, never past the interval."""
    if failures <= 0:
        return interval_s
    return min(interval_s, RETRY_S[min(failures, len(RETRY_S)) - 1])


class PresenceBeat:
    """Calls ``send()`` (True when the site took it) on its own thread.

    ``live_s()`` reads the site's current online window (what its last answer
    said). Wake the loop early with ``poke()`` when something it reports changed.
    """

    def __init__(
        self,
        send: Callable[[], bool],
        live_s: Callable[[], float | None],
        *,
        name: str = "remote-presence",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._send = send
        self._live_s = live_s
        self._clock = clock
        self._name = name
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.failures = 0
        self.last_ok_at = 0.0

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name=self._name, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def poke(self) -> None:
        self._wake.set()

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def beat_once(self) -> float:
        """One beat; returns how long to wait before the next."""
        try:
            ok = bool(self._send())
        except Exception:
            ok = False
        interval = beat_interval(self._live_s())
        if ok:
            self.failures = 0
            self.last_ok_at = self._clock()
            return interval
        self.failures += 1
        return retry_delay(self.failures, interval)

    def _loop(self) -> None:
        while not self._stop.is_set():
            wait = self.beat_once()
            self._wake.wait(wait)
            self._wake.clear()
