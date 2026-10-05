"""Background preparation of a staged video: frames first, then the transcript."""

from __future__ import annotations

import threading
from typing import Any

from backend.agent.video import audio, frames
from backend.agent.video.ffmpeg_install import FfmpegInstallError, ensure_installed
from backend.agent.video.frames import VideoError
from backend.agent.video.staging import resolve_staged

MAX_CONCURRENT_JOBS = 2

_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}
_slots = threading.BoundedSemaphore(MAX_CONCURRENT_JOBS)


def _new_job(frames_total: int, transcribe: bool) -> dict[str, Any]:
    return {
        "state": "queued",
        "frames_done": 0,
        "frames_total": frames_total,
        "transcript": "skipped",
        "transcript_note": "",
        "error": "",
        "sendable": False,
        "transcribe": transcribe,
        "path": None,
    }


def _public(job: dict[str, Any]) -> dict[str, Any]:
    out = {k: job[k] for k in (
        "state", "frames_done", "frames_total", "transcript", "transcript_note", "error"
    )}
    out["sendable"] = bool(job["sendable"]) or job["state"] in ("ready", "error")
    if job["state"] == "extracting" and job["path"] is not None:
        out["frames_done"] = sum(1 for p in frames.frame_paths(job["path"], job["frames_total"]) if p.is_file())
    return out


def prep_status(staged_id: str) -> dict[str, Any]:
    with _lock:
        job = _jobs.get(staged_id)
        if job is None:
            # Unknown (never started, or the app restarted): the send path prepares on demand.
            return {
                "state": "ready", "frames_done": 0, "frames_total": 0,
                "transcript": "skipped", "transcript_note": "", "error": "", "sendable": True,
            }
        return _public(dict(job))


def _set(staged_id: str, **fields: Any) -> None:
    with _lock:
        _jobs[staged_id].update(fields)


def _run(staged_id: str) -> None:
    _slots.acquire()
    released = threading.Event()

    def release_slot() -> None:
        # Exactly once: the transcription network call must not hold a prep slot.
        if not released.is_set():
            released.set()
            _slots.release()

    try:
        with _lock:
            job = _jobs[staged_id]
            n, transcribe, path = job["frames_total"], job["transcribe"], job["path"]
        _set(staged_id, state="preparing_ffmpeg")
        try:
            ensure_installed()
        except FfmpegInstallError as exc:
            raise VideoError(str(exc)) from exc
        _set(staged_id, state="extracting")
        got = frames.extract_frames(path, n)
        _set(staged_id, frames_done=len(got), frames_total=len(got), sendable=True)
        if transcribe:
            _set(staged_id, state="transcribing")
            res = audio.transcribe_video(path, on_extracted=release_slot)
            _set(
                staged_id,
                transcript="ok" if res.text else "none",
                transcript_note=res.note,
            )
        _set(staged_id, state="ready")
    except VideoError as exc:
        _set(staged_id, state="error", error=str(exc))
    except Exception as exc:  # never leave the chip spinning
        _set(staged_id, state="error", error=f"Could not prepare video: {exc}")
    finally:
        release_slot()


def _launch(staged_id: str, frames_n: int, transcribe: bool) -> dict[str, Any]:
    """Insert the job and claim the launch under one lock; concurrent callers get the existing status."""
    try:
        path = resolve_staged(staged_id)
        err = ""
    except VideoError as exc:
        path, err = None, str(exc)
    job = _new_job(frames_n, transcribe)
    job["path"] = path
    if err:
        job.update(state="error", error=err)
    with _lock:
        cur = _jobs.get(staged_id)
        if cur is not None and cur["state"] != "error":
            return _public(dict(cur))
        _jobs[staged_id] = job
        out = _public(dict(job))
    if not err:
        threading.Thread(
            target=_run, args=(staged_id,), name=f"video-prep-{staged_id[:8]}", daemon=True
        ).start()
    return out


def start_prep(staged_id: str, *, frames: int, transcribe: bool) -> dict[str, Any]:  # noqa: A002
    """Idempotent per staged_id: a job that is running or done is left alone."""
    return _launch(staged_id, frames, transcribe)


def retry_prep(staged_id: str) -> dict[str, Any]:
    """Restart a failed (or unknown) job with the settings it was started with."""
    with _lock:
        job = _jobs.get(staged_id)
        n = job["frames_total"] if job else 0
        transcribe = job["transcribe"] if job else True
    if n <= 0:
        from backend.agent.video.limits import video_limits

        n = video_limits().frames_per_video
    return _launch(staged_id, n, transcribe)
