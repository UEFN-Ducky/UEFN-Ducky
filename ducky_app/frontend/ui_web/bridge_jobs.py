"""Non-blocking background jobs for the pywebview JS bridge.

Long work (LLM, Store download, skill draft, MCP catalog, key test, …) must not
run on the bridge thread — one blocked ``api.*`` call freezes the whole panel
(file tree, chat, agents). Callers ``job_start`` then ``job_poll`` with short
bridge round-trips.
"""

from __future__ import annotations

import concurrent.futures
import threading
import uuid
from collections.abc import Callable
from typing import Any

# Parallelism for Store + LLM + skill draft + key tests at once.
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=6, thread_name_prefix="bridge-job")
_JOBS: dict[str, concurrent.futures.Future] = {}
_CANCELLED: set[str] = set()
_LOCK = threading.Lock()


def job_start(fn: Callable[[], Any]) -> dict[str, Any]:
    """Submit ``fn`` on a worker; return immediately with ``job_id``."""
    job_id = uuid.uuid4().hex
    future = _EXECUTOR.submit(fn)
    with _LOCK:
        stale = [jid for jid, fut in _JOBS.items() if fut.done()]
        for jid in stale[:48]:
            _JOBS.pop(jid, None)
            _CANCELLED.discard(jid)
        _JOBS[job_id] = future
    return {"ok": True, "job_id": job_id, "pending": True}


def job_cancel(job_id: str) -> dict[str, Any]:
    """Mark a job cancelled. Stops poll waiters; running workers may still finish (ponytail: can't kill threads)."""
    jid = (job_id or "").strip()
    if not jid:
        return {"ok": False, "error": "job_id required"}
    with _LOCK:
        _CANCELLED.add(jid)
        future = _JOBS.get(jid)
    if future is not None:
        future.cancel()
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
            _JOBS.pop(jid, None)
            _CANCELLED.discard(jid)
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
        _JOBS.pop(jid, None)
        _CANCELLED.discard(jid)
    if isinstance(result, dict):
        out = dict(result)
        out["pending"] = False
        out["job_id"] = jid
        return out
    return {"ok": True, "pending": False, "result": result, "job_id": jid}


def job_wait(job_id: str, *, timeout: float = 90.0, poll_s: float = 0.05) -> dict[str, Any]:
    """Sync wait for tests / legacy callers — still holds the calling thread."""
    import time

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
        fut = _JOBS.pop(jid, None)
    if fut is not None:
        fut.cancel()
    return {"ok": False, "error": f"Job timed out after {int(timeout)}s"}
