"""Debounce the UEFN listener's offline -> online flips.

A busy UEFN misses health pings, so the listener flaps between online and offline
many times a minute. Each flip used to start a full re-ship (skills to every IDE,
IDE bridges, listener sync). The listener now has to stay online for ``hold_s``
before the flip counts, and it counts once per online stretch.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

ONLINE_HOLD_SEC = 10.0


class OnlineDebouncer:
    """``observe(online)`` returns True once per online stretch that lasted ``hold_s``."""

    def __init__(self, hold_s: float = ONLINE_HOLD_SEC, clock: Callable[[], float] = time.monotonic) -> None:
        self.hold_s = float(hold_s)
        self._clock = clock
        self._lock = threading.Lock()
        self._online_since: float | None = None
        self._fired = False

    def observe(self, online: bool) -> bool:
        with self._lock:
            if not online:
                self._online_since = None
                self._fired = False
                return False
            now = self._clock()
            if self._online_since is None:
                self._online_since = now
                self._fired = False
            if self._fired or now - self._online_since < self.hold_s:
                return False
            self._fired = True
            return True

    def reset(self) -> None:
        with self._lock:
            self._online_since = None
            self._fired = False
