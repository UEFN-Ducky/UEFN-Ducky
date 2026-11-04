"""Terminal output to the window, paced so busy agent terminals cost the UI little.

The PTY reader used to send one websocket frame per read: a pytest run in an agent's
terminal sent thousands of frames a minute into a tab nobody looked at, and WebView2
sat at 100% CPU. Now each session's output goes through an ``OutputPacer``:

* shown terminal: output is batched, at most ``fps`` frames a second
* hidden terminal (a window said it is not on screen): nothing but one small status
  frame a second; the session's kept history holds the text, and showing the tab
  again resets the screen and replays that history

One ``PacerHub`` thread serves every pacer, so a hundred terminals add no threads.
"""

from __future__ import annotations

import heapq
import itertools
import threading
import time
from typing import Any, Callable

VISIBLE_FPS = 30.0
HIDDEN_GAP_SEC = 1.0
# xterm "full reset": the replay that follows redraws the whole history once.
RESET_SEQ = "\x1bc"


class PacerHub:
    """One daemon thread that calls ``tick()`` on pacers when their next frame is due."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._cond = threading.Condition()
        self._heap: list[tuple[float, int, "OutputPacer"]] = []
        self._seq = itertools.count()
        self._thread: threading.Thread | None = None

    def schedule(self, pacer: "OutputPacer", at: float) -> None:
        with self._cond:
            heapq.heappush(self._heap, (at, next(self._seq), pacer))
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, daemon=True, name="terminal-pacer")
                self._thread.start()
            self._cond.notify()

    def _loop(self) -> None:
        while True:
            with self._cond:
                while not self._heap:
                    self._cond.wait()
                at, _, pacer = self._heap[0]
                wait = at - self._clock()
                if wait > 0:
                    self._cond.wait(wait)
                    continue
                heapq.heappop(self._heap)
            try:
                pacer.tick()
            except Exception:  # noqa: BLE001 — one broken socket must not stop the others
                pass


_HUB = PacerHub()


class OutputPacer:
    def __init__(
        self,
        send_output: Callable[[str], None],
        send_status: Callable[[dict[str, Any]], None],
        *,
        replay: Callable[[], str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        hub: PacerHub | None = _HUB,
        fps: float = VISIBLE_FPS,
        hidden_gap_s: float = HIDDEN_GAP_SEC,
    ) -> None:
        self._send_output = send_output
        self._send_status = send_status
        self._replay = replay
        self._clock = clock
        self._hub = hub
        self.frame_gap_s = 1.0 / float(fps)
        self.hidden_gap_s = float(hidden_gap_s)
        self._lock = threading.Lock()
        # Held while taking and sending a frame: the reader and the hub thread both tick,
        # and frames must reach the socket in order.
        self._send_lock = threading.Lock()
        self._visible = True
        self._buf: list[str] = []
        self._hidden_chars = 0
        self._missed = False  # output went by while hidden: replay when shown
        self._last_frame = float("-inf")
        self._last_summary = float("-inf")
        self._scheduled: float | None = None

    @property
    def visible(self) -> bool:
        return self._visible

    def push(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            if self._visible:
                self._buf.append(text)
            else:
                self._hidden_chars += len(text)
                self._missed = True
            due = self._next_due_locked()
        if due is not None and due <= self._clock():
            self.tick()  # nothing sent lately: send at once (typing echo stays instant)
        elif due is not None:
            self._schedule(due)

    def tick(self) -> None:
        """Send what is due now; schedule the next frame when more waits."""
        with self._send_lock:
            due = self._tick_locked()
        if due is not None:
            self._schedule(due)

    def _tick_locked(self) -> float | None:
        now = self._clock()
        data = ""
        summary: dict[str, Any] | None = None
        with self._lock:
            self._scheduled = None
            if self._visible and self._buf and now - self._last_frame >= self.frame_gap_s:
                data = "".join(self._buf)
                self._buf.clear()
                self._last_frame = now
            if not self._visible and self._hidden_chars and now - self._last_summary >= self.hidden_gap_s:
                summary = {"hidden_output_chars": self._hidden_chars}
                self._hidden_chars = 0
                self._last_summary = now
            due = self._next_due_locked()
        if data:
            self._send_output(data)
        if summary is not None:
            self._send_status(summary)
        return due

    def set_visible(self, visible: bool) -> None:
        with self._send_lock:
            self._set_visible_locked(bool(visible))

    def _set_visible_locked(self, visible: bool) -> None:
        with self._lock:
            if visible == self._visible:
                return
            self._visible = visible
            reveal = False
            if not visible:
                if self._buf:  # never sent: the replay brings it back
                    self._missed = True
                    self._buf.clear()
            elif self._missed:
                reveal = True
                self._missed = False
                self._hidden_chars = 0
        if reveal and self._replay is not None:
            self._send_output(RESET_SEQ + self._replay())
            with self._lock:
                self._last_frame = self._clock()

    def flush(self) -> None:
        """Send buffered output now (before the exit frame), whatever the frame gap."""
        with self._send_lock:
            with self._lock:
                self._last_frame = float("-inf")
            self._tick_locked()

    def drop_pending(self) -> None:
        """A client just attached and got the whole history: nothing buffered is news to it."""
        with self._send_lock, self._lock:
            self._buf.clear()
            self._missed = False
            self._hidden_chars = 0

    def _next_due_locked(self) -> float | None:
        if self._visible and self._buf:
            return self._last_frame + self.frame_gap_s
        if not self._visible and self._hidden_chars:
            return self._last_summary + self.hidden_gap_s
        return None

    def _schedule(self, due: float) -> None:
        if self._hub is None:
            return
        with self._lock:
            if self._scheduled is not None and self._scheduled <= due:
                return
            self._scheduled = due
        self._hub.schedule(self, due)
