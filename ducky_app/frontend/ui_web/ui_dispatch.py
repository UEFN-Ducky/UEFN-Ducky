"""Serialize pywebview evaluate_js / window ops to avoid cross-thread deadlocks.

One dispatch queue PER WINDOW: pywebview's evaluate_js has no timeout, so a
wedged WebView2 (hidden / closing / suspended focus window) used to head-of-line
block the single shared queue and freeze UI pushes to EVERY window — the classic
"panel locks up, then tool cards flood the chat" symptom. Now only the wedged
window stalls.

A watchdog samples all Python thread stacks when any evaluate_js is in flight
longer than _STALL_DUMP_AFTER_SEC and records them to the perf session file
(kind="ui_js_stall"), so lockups are diagnosable after the fact.
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
from collections.abc import Callable
from typing import Any
from weakref import WeakValueDictionary

_STALL_DUMP_AFTER_SEC = 3.0
# Scripts already queued for a window go in ONE evaluate_js: each call costs a 20-90 ms
# UI-thread round trip whatever its size, so a burst of small replies froze the window.
_BATCH_MAX_BYTES = 256 * 1024

_lock = threading.Lock()
_channels: dict[int, _Channel] = {}
# A late producer must not recreate a worker for a destroyed window. Weak values
# avoid retaining those windows or confusing a later object that reuses an id.
_closed_windows: WeakValueDictionary[int, Any] = WeakValueDictionary()
_watchdog_started = False


class _Channel:
    """One serialized dispatch worker for a single pywebview window (or the ops channel)."""

    def __init__(self, label: str) -> None:
        self.queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.label = label
        self._admission_lock = threading.Lock()
        self._stopped = False
        # monotonic timestamp of the evaluate_js currently inside pywebview; 0 when idle
        self.inflight_since = 0.0
        self.inflight_js_bytes = 0
        self.stall_dumped = False
        threading.Thread(target=self._worker, name=f"ui-dispatch-{label}", daemon=True).start()

    def submit(self, kind: str, payload: Any) -> None:
        with self._admission_lock:
            if not self._stopped:
                self.queue.put((kind, payload))

    def stop(self) -> None:
        """Release pending work even if the native call in flight never returns."""
        with self._admission_lock:
            if self._stopped:
                return
            self._stopped = True
            while True:
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    break
                else:
                    self.queue.task_done()
            self.queue.put(("stop", None))

    def _worker(self) -> None:
        held: tuple[str, Any] | None = None
        while True:
            kind, payload = held if held is not None else self.queue.get()
            held = None
            extra = 0
            try:
                if kind == "stop":
                    return
                if self._stopped:
                    continue
                if kind == "js":
                    batch = [payload]
                    size = len(payload[1]) if isinstance(payload[1], str) else 0
                    while size < _BATCH_MAX_BYTES:
                        try:
                            nxt = self.queue.get_nowait()
                        except queue.Empty:
                            break
                        if nxt[0] != "js" or nxt[1][0] is not payload[0]:
                            held = nxt  # a call or a stop keeps its place: right after this batch
                            break
                        extra += 1
                        batch.append(nxt[1])
                        size += len(nxt[1][1]) if isinstance(nxt[1][1], str) else 0
                    self._run_js_batch(batch)
                elif kind == "call":
                    payload()
            except Exception:
                pass
            finally:
                # queue.get() blocks before assigning the next payload. Without
                # this, an idle worker pins its last script/closure and window.
                del payload
                batch = []
                for _ in range(1 + extra):
                    self.queue.task_done()

    def _run_js_batch(self, batch: list[tuple[Any, ...]]) -> None:
        if len(batch) == 1:
            self._run_js(batch[0])
            return
        window = batch[0][0]
        # Each script in its own try: one that throws must not drop the replies after it.
        js = "\n".join(f"try{{{p[1]}\n}}catch(e){{console.error(e)}}" for p in batch)
        enqueued_at = batch[0][2]
        self._run_js((window, js, enqueued_at, batch[0][3], ""), parts=batch)

    def _run_js(self, payload: tuple[Any, ...], parts: list[tuple[Any, ...]] | None = None) -> None:
        window, js, enqueued_at, depth, *rest = payload
        source = str(rest[0]) if rest and rest[0] else ""
        if window is None:
            return
        t0 = time.perf_counter()
        self.inflight_js_bytes = len(js) if isinstance(js, str) else 0
        self.stall_dumped = False
        self.inflight_since = time.monotonic()
        try:
            window.evaluate_js(js)
        finally:
            self.inflight_since = 0.0
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            wait_ms = max(0.0, (t0 - enqueued_at) * 1000.0) if enqueued_at else 0.0
            try:
                from frontend.perf_trace import trace

                # One row per reply (its own source and bytes) so per-method counts stay true.
                rows = parts or [payload]
                for part in rows:
                    part_js = part[1]
                    part_src = str(part[4]) if len(part) > 4 and part[4] else ""
                    trace(
                        "ui_js",
                        "evaluate_js",
                        elapsed_ms / len(rows),
                        js_bytes=len(part_js) if isinstance(part_js, str) else 0,
                        queue_depth=int(part[3] or 0),
                        queue_wait_ms=round(wait_ms, 3),
                        window=self.label,
                        source=part_src or None,
                        batch=len(rows) if parts else None,
                    )
            except Exception:
                pass


def _label_for(window: Any) -> str:
    try:
        from frontend.ui_web import focus_windows

        if focus_windows.is_main_window(window):
            return "main"
        title = str(getattr(window, "title", "") or "")
        return f"focus:{title[:40]}" if title else "focus"
    except Exception:
        return "win"


def _channel_for(window: Any) -> _Channel | None:
    key = id(window) if window is not None else 0
    with _lock:
        if window is not None and _closed_windows.get(key) is window:
            return None
        ch = _channels.get(key)
        if ch is None:
            label = "ops" if window is None else _label_for(window)
            ch = _Channel(label)
            _channels[key] = ch
    _ensure_watchdog()
    return ch


def drop_window(window: Any) -> None:
    """Stop and forget the dispatch channel of a destroyed window."""
    if window is None:
        return
    with _lock:
        try:
            _closed_windows[id(window)] = window
        except TypeError:
            # Actual pywebview windows support weak references; keep disposal
            # safe for minimal native adapters that do not.
            pass
        ch = _channels.pop(id(window), None)
    if ch is not None:
        ch.stop()


def _dump_stall(ch: _Channel, waited_sec: float) -> None:
    """Record every Python thread's stack while an evaluate_js is stuck — the
    blocking thread (GIL hog, wedged Invoke, deadlock) shows up here."""
    try:
        import sys

        names = {t.ident: t.name for t in threading.enumerate()}
        stacks: dict[str, str] = {}
        for ident, frame in sys._current_frames().items():
            name = names.get(ident, f"tid-{ident}")
            stacks[name] = "".join(traceback.format_stack(frame)[-8:])[-2400:]
        from frontend.perf_trace import trace

        trace(
            "ui_js_stall",
            ch.label,
            waited_sec * 1000.0,
            js_bytes=ch.inflight_js_bytes,
            thread_stacks=stacks,
        )
    except Exception:
        pass


def _watchdog() -> None:
    while True:
        time.sleep(1.0)
        try:
            with _lock:
                chans = list(_channels.values())
            now = time.monotonic()
            for ch in chans:
                t0 = ch.inflight_since
                if t0 and not ch.stall_dumped and now - t0 >= _STALL_DUMP_AFTER_SEC:
                    ch.stall_dumped = True
                    _dump_stall(ch, now - t0)
        except Exception:
            pass


def _ensure_watchdog() -> None:
    global _watchdog_started
    with _lock:
        if _watchdog_started:
            return
        _watchdog_started = True
    threading.Thread(target=_watchdog, name="ui-dispatch-watchdog", daemon=True).start()


def ensure_started() -> None:
    """Kept for callers that warmed up the old single worker; starts the watchdog."""
    _ensure_watchdog()


def schedule_evaluate_js(window: Any, js: str, *, source: str = "") -> None:
    """``source`` names what the script carries (the panel API method a bridge
    reply answers), so the perf log says which call sends the big payloads."""
    if window is None or not js:
        return
    ch = _channel_for(window)
    if ch is None:
        return
    depth = ch.queue.qsize()
    ch.submit("js", (window, js, time.perf_counter(), depth, source))


def schedule_call(fn: Callable[[], None]) -> None:
    ch = _channel_for(None)
    if ch is not None:
        ch.submit("call", fn)
