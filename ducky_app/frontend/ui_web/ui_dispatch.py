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

_STALL_DUMP_AFTER_SEC = 3.0

_lock = threading.Lock()
_channels: dict[int, _Channel] = {}
_watchdog_started = False


class _Channel:
    """One serialized dispatch worker for a single pywebview window (or the ops channel)."""

    def __init__(self, label: str) -> None:
        self.queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.label = label
        # monotonic timestamp of the evaluate_js currently inside pywebview; 0 when idle
        self.inflight_since = 0.0
        self.inflight_js_bytes = 0
        self.stall_dumped = False
        threading.Thread(target=self._worker, name=f"ui-dispatch-{label}", daemon=True).start()

    def _worker(self) -> None:
        while True:
            kind, payload = self.queue.get()
            try:
                if kind == "stop":
                    return
                if kind == "js":
                    self._run_js(payload)
                elif kind == "call":
                    payload()
            except Exception:
                pass
            finally:
                self.queue.task_done()

    def _run_js(self, payload: tuple[Any, str, float, int]) -> None:
        window, js, enqueued_at, depth = payload
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

                trace(
                    "ui_js",
                    "evaluate_js",
                    elapsed_ms,
                    js_bytes=self.inflight_js_bytes,
                    queue_depth=int(depth or 0),
                    queue_wait_ms=round(wait_ms, 3),
                    window=self.label,
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


def _channel_for(window: Any) -> _Channel:
    key = id(window) if window is not None else 0
    with _lock:
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
        ch = _channels.pop(id(window), None)
    if ch is not None:
        ch.queue.put(("stop", None))


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


def schedule_evaluate_js(window: Any, js: str) -> None:
    if window is None or not js:
        return
    ch = _channel_for(window)
    depth = ch.queue.qsize()
    ch.queue.put(("js", (window, js, time.perf_counter(), depth)))


def schedule_call(fn: Callable[[], None]) -> None:
    _channel_for(None).queue.put(("call", fn))
