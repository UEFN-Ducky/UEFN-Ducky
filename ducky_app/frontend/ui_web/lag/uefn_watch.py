"""Ducky notices UEFN by itself: it opened, it closed, its listener came up or went away.

From Nov 3 2026 the listener status was fetched only when the person pressed refresh or a
UEFN tool ran, and nothing restarted a listener that had not started. The header then said
"Not checked" or "Offline" beside a running UEFN until Restart UEFN listener was pressed:
after every island switch, after every UEFN start, and for most new PCs for good (Nov 21
2026: 40 of 45 never read "Connected to UEFN").

This follows the process list, which opens no connection. With UEFN closed nothing is ever
fetched. With UEFN open the status is fetched every ``OFFLINE_EVERY_S`` until the listener
answers and every ``ONLINE_EVERY_S`` after that (one loopback GET the listener's own thread
answers, never the editor's), and a listener that stays offline beside a running UEFN MCP is
restarted (``backend.bridge.revive.AutoRevive``).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

_log = logging.getLogger(__name__)

TICK_S = 5.0
# UEFN is open and not linked yet: the island is loading or Python is off.
OFFLINE_EVERY_S = 10.0
# Linked: often enough to see an island switch take the listener away.
ONLINE_EVERY_S = 30.0


class UefnWatch:
    """One round at a time (:meth:`tick`); every outside thing is passed in, for tests."""

    def __init__(
        self,
        *,
        running: Callable[[], bool],
        last_status: Callable[[], dict[str, Any] | None],
        refresh: Callable[[], None],
        observe: Callable[[dict[str, Any]], Any],
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._running = running
        self._last_status = last_status
        self._refresh = refresh
        self._observe = observe
        self._now = now
        self._was_running: bool | None = None  # None: no round yet
        self._next = 0.0

    def tick(self) -> bool:
        """One round. True when it asked for a fresh status."""
        running = bool(self._running())
        if not running:
            # Once, so the header says Offline instead of Not checked or a stale Connected.
            # A status fetch opens no connection while UEFN is closed.
            asked = self._was_running is not False
            self._was_running = False
            if asked:
                self._refresh()
                self._observe({})  # UEFN is gone: its next start gets its own restart tries
            return asked
        now = self._now()
        if not self._was_running:  # UEFN just opened (or this is the first round)
            self._was_running = True
            self._next = now
        status = self._last_status() or {}
        asked = now >= self._next
        if asked:
            self._next = now + (ONLINE_EVERY_S if status.get("online") else OFFLINE_EVERY_S)
            self._refresh()
        # Offline beside a running UEFN MCP for a while: restart the listener. A status from
        # before UEFN opened has no UEFN MCP in it, so it starts nothing.
        self._observe(status)
        return asked


_started = False
_start_lock = threading.Lock()


def start(api: Any) -> bool:
    """Start the watcher once per process (the window or the launcher's backend). True when
    this call started it."""
    global _started
    with _start_lock:
        if _started:
            return False
        _started = True
    from backend.bridge import editor_presence
    from backend.bridge.revive import AutoRevive
    from frontend.settings import UEFN_LISTENER_PORT

    auto = AutoRevive()
    watch = UefnWatch(
        running=editor_presence.uefn_running,
        last_status=lambda: getattr(api, "_last_listener_status", None),
        refresh=lambda: api._listener_status_cache().refresh(),
        observe=lambda status: auto.observe(status, UEFN_LISTENER_PORT),
    )

    def _loop() -> None:
        while True:
            try:
                watch.tick()
            except Exception:  # noqa: BLE001 — one bad round never stops the watcher
                _log.debug("UEFN watch round failed", exc_info=True)
            time.sleep(TICK_S)

    threading.Thread(target=_loop, name="uefn-watch", daemon=True).start()
    return True
