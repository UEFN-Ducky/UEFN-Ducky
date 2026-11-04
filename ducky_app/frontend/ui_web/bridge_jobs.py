"""Non-blocking background jobs for the pywebview JS bridge.

Long work (LLM, Store download, skill draft, MCP catalog, key test, …) must not
run on the bridge thread — one blocked ``api.*`` call freezes the whole panel
(file tree, chat, agents). Callers ``job_start`` then ``job_poll`` with short
bridge round-trips.

Each job also pushes ``bridge_job_progress`` events (job id, state, percent) when its
state or progress changes, at most 2 a second per job, so pages can stop polling.
A job reports progress with ``job_progress(percent)`` from inside its function.
"""

from __future__ import annotations

import atexit
import concurrent.futures
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

# Parallelism for Store + LLM + skill draft + key tests at once.
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=6, thread_name_prefix="bridge-job")
_JOBS: dict[str, concurrent.futures.Future] = {}
_JOB_CANCEL: dict[str, threading.Event] = {}
_JOB_COMPLETED_AT: dict[str, float] = {}
_CANCELLED: dict[str, float] = {}
_LOCK = threading.Lock()
_MAX_JOBS = 128
# Bound the executor's otherwise unbounded submission queue, including cancelled
# jobs whose Python functions are still running.
_SLOTS = threading.BoundedSemaphore(64)
_REAPER_STOP = threading.Event()
_reaper_started = False

# Instant jobs (Cursor key length-check) finish before the JS 100ms first poll.
# Never drop a done Future until that grace window — otherwise poll returns
# "unknown or expired job" and the UI shows bare "Test failed" after a real save.
_STALE_GRACE_S = 120.0
_REAP_INTERVAL_S = 30.0
atexit.register(_REAPER_STOP.set)

# bridge_job_progress pushes per job: at most one per gap (the last value always goes).
_PUSH_GAP_S = 0.5
_FINAL_STATES = frozenset({"done", "error", "cancelled"})
# job id -> {"value", "sent", "at", "timer"}: coalesced progress pushes.
_PROGRESS: dict[str, dict[str, Any]] = {}
_current = threading.local()


def _emit(event: dict[str, Any]) -> None:
    try:
        from frontend.ui_web.agent_turns.push import get_panel_push

        push = get_panel_push()
        if push:
            push(event)
    except Exception:
        pass


def _progress_changed(jid: str, state: str, percent: float | None) -> None:
    pct = None if percent is None else max(0, min(100, int(percent)))
    with _LOCK:
        entry = _PROGRESS.get(jid)
        if entry is None:
            if jid not in _JOBS:
                return  # cancelled or expired: nobody listens
            entry = _PROGRESS[jid] = {"sent": None, "at": 0.0, "timer": None}
        if pct is None and entry.get("value"):
            pct = entry["value"][1]
        entry["value"] = (state, pct)
        if entry["timer"] is not None:
            return
        wait = entry["at"] + _PUSH_GAP_S - time.monotonic()
        if wait > 0:
            timer = threading.Timer(wait, _flush_progress, args=(jid,))
            timer.daemon = True
            entry["timer"] = timer
            timer.start()
            return
    _flush_progress(jid)


def _flush_progress(jid: str) -> None:
    with _LOCK:
        entry = _PROGRESS.get(jid)
        if entry is None:
            return
        entry["timer"] = None
        value = entry["value"]
        if value[0] in _FINAL_STATES:
            _PROGRESS.pop(jid, None)
        if value == entry["sent"]:
            return
        entry["sent"] = value
        entry["at"] = time.monotonic()
    _emit({"type": "bridge_job_progress", "job_id": jid, "state": value[0], "percent": value[1]})


def job_progress(percent: float, state: str = "running", job_id: str = "") -> None:
    """Report a job's progress (0-100); inside a job's function ``job_id`` is implied."""
    jid = (job_id or getattr(_current, "job_id", "") or "").strip()
    if jid:
        _progress_changed(jid, state, percent)


def _forget_locked(jid: str) -> threading.Event | None:
    _JOB_COMPLETED_AT.pop(jid, None)
    _JOBS.pop(jid, None)
    return _JOB_CANCEL.pop(jid, None)


def _reap_expired(now: float) -> None:
    with _LOCK:
        for jid, completed_at in list(_JOB_COMPLETED_AT.items()):
            if now - completed_at >= _STALE_GRACE_S:
                _forget_locked(jid)
        for jid, cancelled_at in list(_CANCELLED.items()):
            if now - cancelled_at >= _STALE_GRACE_S:
                _CANCELLED.pop(jid, None)


def _reaper() -> None:
    while not _REAPER_STOP.wait(_REAP_INTERVAL_S):
        _reap_expired(time.monotonic())


def _completed(jid: str, future: concurrent.futures.Future) -> None:
    with _LOCK:
        if _JOBS.get(jid) is not future:
            return
        _JOB_COMPLETED_AT[jid] = time.monotonic()
    failed = future.cancelled() or future.exception() is not None
    _progress_changed(jid, "error" if failed else "done", None if failed else 100)


def job_start(fn: Callable[[], Any]) -> dict[str, Any]:
    """Submit ``fn`` on a worker; return immediately with ``job_id``."""
    global _reaper_started
    job_id = uuid.uuid4().hex
    cancelled = threading.Event()
    slots = _SLOTS

    def run() -> Any:
        _current.job_id = job_id
        try:
            if not cancelled.is_set():
                return fn()
        finally:
            _current.job_id = ""
            slots.release()

    _reap_expired(time.monotonic())
    with _LOCK:
        if len(_JOBS) >= _MAX_JOBS or not slots.acquire(blocking=False):
            return {"ok": False, "pending": False, "error": "Too many background jobs; wait for existing jobs to finish."}
        try:
            future = _EXECUTOR.submit(run)
        except Exception:
            slots.release()
            raise
        _JOBS[job_id] = future
        _JOB_CANCEL[job_id] = cancelled
        if not _reaper_started:
            threading.Thread(target=_reaper, daemon=True, name="bridge-job-reaper").start()
            _reaper_started = True
    # Already-complete futures invoke callbacks synchronously: register outside
    # the lock, and only after the future has been published to pollers.
    future.add_done_callback(lambda done: _completed(job_id, done))
    return {"ok": True, "job_id": job_id, "pending": True}


def job_cancel(job_id: str) -> dict[str, Any]:
    """Mark a job cancelled. Stops poll waiters; running workers may still finish (ponytail: can't kill threads)."""
    jid = (job_id or "").strip()
    if not jid:
        return {"ok": False, "error": "job_id required"}
    with _LOCK:
        cancelled = _forget_locked(jid)
        if cancelled is not None:
            if len(_CANCELLED) >= _MAX_JOBS:
                _CANCELLED.pop(next(iter(_CANCELLED)))
            _CANCELLED[jid] = time.monotonic()
    if cancelled is not None:
        # Future.cancel() leaves a work item queued in ThreadPoolExecutor. Keep
        # the slot until our wrapper is actually dequeued, then skip its work.
        cancelled.set()
        with _LOCK:
            entry = _PROGRESS.pop(jid, None)
        if entry and entry["timer"] is not None:
            entry["timer"].cancel()
        _emit({"type": "bridge_job_progress", "job_id": jid, "state": "cancelled", "percent": None})
    return {"ok": True, "cancelled": True, "job_id": jid}


def job_poll(job_id: str) -> dict[str, Any]:
    """Non-blocking status check. Bridge-safe (returns in ms)."""
    jid = (job_id or "").strip()
    if not jid:
        return {"ok": False, "error": "job_id required"}
    with _LOCK:
        cancelled = jid in _CANCELLED
        future = _JOBS.get(jid)
    if cancelled:
        with _LOCK:
            _forget_locked(jid)
            _CANCELLED.pop(jid, None)
        return {
            "ok": False,
            "pending": False,
            "cancelled": True,
            "error": "cancelled",
            "job_id": jid,
        }
    if future is None:
        return {"ok": False, "error": "unknown or expired job"}
    if not future.done():
        return {"ok": True, "pending": True, "job_id": jid}
    try:
        result = future.result(timeout=0)
    except Exception as exc:
        result = {"ok": False, "error": str(exc) or "job failed"}
    with _LOCK:
        _forget_locked(jid)
        _CANCELLED.pop(jid, None)
    if isinstance(result, dict):
        out = dict(result)
        out["pending"] = False
        out["job_id"] = jid
        return out
    return {"ok": True, "pending": False, "result": result, "job_id": jid}


def job_wait(job_id: str, *, timeout: float = 90.0, poll_s: float = 0.05) -> dict[str, Any]:
    """Sync wait for tests / legacy callers — still holds the calling thread."""
    jid = (job_id or "").strip()
    if not jid:
        return {"ok": False, "error": "job_id required"}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        polled = job_poll(jid)
        if not polled.get("pending"):
            return polled
        time.sleep(poll_s)
    with _LOCK:
        cancelled = _forget_locked(jid)
        _CANCELLED.pop(jid, None)
    if cancelled is not None:
        cancelled.set()
    return {"ok": False, "error": f"Job timed out after {int(timeout)}s"}
