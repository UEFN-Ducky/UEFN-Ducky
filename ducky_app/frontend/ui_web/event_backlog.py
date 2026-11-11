"""The panel event backlog: recent events kept for long-polling windows, capped by count
and by size, oldest dropped first.

Capped by size too: tool events carry whole file texts (write arguments, before/after of
an edit), and a burst of big edits stayed pinned here for as long as the app sat idle
afterwards. The size is JSON bytes; Python holds about twice that (8 MB of JSON measured
15 MB on Nov 5 2026), so 4 MB here. Not thread-safe: panel_httpd holds its lock.
"""

from __future__ import annotations

from collections import deque

MAX_EVENTS = 4000
MAX_BYTES = 4 * 1024 * 1024


class EventBacklog:
    def __init__(self, max_events: int = MAX_EVENTS, max_bytes: int = MAX_BYTES) -> None:
        self.rows: deque[tuple[int, dict[str, object], int]] = deque()  # (seq, event, size)
        self.bytes = 0
        self.max_events = max_events
        self.max_bytes = max_bytes
        # The newest event dropped to make room: a window that last read before it missed events.
        self.dropped_through = 0

    def add(self, seq: int, event: dict[str, object]) -> None:
        size = event_size(event)
        self.rows.append((seq, event, size))
        self.bytes += size
        # The newest event always stays, however big, so pollers still receive it.
        while len(self.rows) > 1 and (len(self.rows) > self.max_events or self.bytes > self.max_bytes):
            dropped_seq, _event, dropped_size = self.rows.popleft()
            self.bytes -= dropped_size
            self.dropped_through = max(self.dropped_through, dropped_seq)

    def missed(self, seq: int) -> bool:
        """True when events after ``seq`` were dropped before a window that read up to ``seq``
        could get them (0: a window that has read nothing yet loads its state itself)."""
        return 0 < seq < self.dropped_through

    def since(self, seq: int, limit: int = 500) -> list[tuple[int, dict[str, object]]]:
        return [(s, event) for s, event, _size in self.rows if s > seq][0:limit]


    def with_resync(self, seq: int, events: list[dict[str, object]]) -> list[dict[str, object]]:
        """``events`` for a window that read up to ``seq``, led by a ``remote_resync`` when it
        fell behind and events it never got were dropped (a burst of big tool events, a busy
        app). Without it the window sat on a stale chat: a tool card spinning, a turn that
        never ends. The page reloads what it shows on that event, as the phone does after
        its link drops (remote/protocol.ts REMOTE_RESYNC)."""
        if not self.missed(seq):
            return events
        return [{"type": "remote_resync", "reason": "events_missed"}, *events]


def event_size(event: dict[str, object]) -> int:
    """About how many bytes the event takes as JSON, without serializing it."""
    size = 0
    stack: list[object] = [event]
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            size += len(value) + 2
        elif isinstance(value, dict):
            size += 2
            for key, item in value.items():
                size += len(str(key)) + 4
                stack.append(item)
        elif isinstance(value, (list, tuple)):
            size += 2 + len(value)
            stack.extend(value)
        else:
            size += 24
    return size
