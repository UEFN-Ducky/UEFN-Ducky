"""The UEFN listener status: the last value we know, never fetched in the background.

Ducky never looks for UEFN on its own (decision 2026-11-03). Reading the status
(``get``) returns the last value at once and never contacts UEFN. Only ``refresh``
fetches: the person pressed refresh, the listener was restarted, or a UEFN tool call
just ran. Many refreshes at once start one fetch; a refresh during a fetch queues one
more. Each fetch's result goes to ``on_result`` (the window's status pill).
"""

from __future__ import annotations

import threading
from typing import Any, Callable


class ListenerStatusCache:
    def __init__(
        self,
        fetch: Callable[[], dict[str, Any]],
        placeholder: Callable[[], dict[str, Any]],
        *,
        on_result: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._fetch = fetch
        self._placeholder = placeholder
        self._on_result = on_result
        self._lock = threading.Lock()
        self._value: dict[str, Any] | None = None
        self._inflight = False
        self._again = False  # refresh() while a fetch ran: that fetch may predate the change
        self.fetches = 0

    @property
    def value(self) -> dict[str, Any] | None:
        return self._value

    def get(self) -> dict[str, Any]:
        """The last status (a copy), or the placeholder before any fetch. Never contacts UEFN."""
        value = self._value
        return dict(value) if value is not None else self._placeholder()

    def refresh(self) -> None:
        """Fetch now, in the background (never blocks the caller)."""
        with self._lock:
            if self._inflight:
                self._again = True
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
                self.fetches += 1
                again, self._again = self._again, False
                if not again:
                    self._inflight = False
            if self._on_result is not None:
                try:
                    self._on_result(dict(result))
                except Exception:  # noqa: BLE001
                    pass
            if not again:
                return
