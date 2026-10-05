"""Send-time video preparation: extract frames for recipients without native video."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from backend.agent.video.ffmpeg_install import binaries
from backend.agent.video.audio import transcribe_video
from backend.agent.video.frames import VideoError, extract_frames
from backend.agent.video.limits import media_limits_for
from backend.agent.video.routing import needs_frames


def prepare_video_frames(
    stored: list[dict[str, Any]],
    *,
    conv_dir: Path,
    provider: str,
    external: bool,
    push_status: Callable[[str], None] | None = None,
    model: str = "",
) -> None:
    videos = [r for r in stored if r.get("kind") == "video" and r.get("path")]
    if not videos:
        return
    limits = media_limits_for(provider, model)
    todo = [
        r
        for r in videos
        if needs_frames(
            str(r.get("mime") or ""), int(r.get("size_bytes") or 0), provider=provider, external=external,
            model=model,
        )
    ]
    images = sum(1 for r in stored if r.get("kind") == "image")
    total = images + len(todo) * limits.frames_per_video
    if total > limits.max_images_per_message:
        raise VideoError(
            f"Too many images for one message ({total} including video frames, limit "
            f"{limits.max_images_per_message}). Lower 'Frames per video' in Settings → Videos "
            "or attach fewer files."
        )
    if todo and binaries() is None and push_status:
        push_status("Downloading ffmpeg for video frames…")
    for r in todo:
        if push_status:
            push_status(f"Extracting frames from {r.get('name') or 'video'}…")
        frames = extract_frames(conv_dir / str(r["path"]), limits.frames_per_video)
        r["frames"] = [{"path": f"attachments/{f.path.name}", "t_s": f.t_s} for f in frames]
        _ensure_transcript(r, conv_dir, push_status)


def _ensure_transcript(
    row: dict[str, Any], conv_dir: Path, push_status: Callable[[str], None] | None
) -> bool:
    """Best-effort: fill transcript/transcript_note once per row. True when the row changed."""
    if "transcript_note" in row:
        return False
    name = row.get("name") or "video"
    if push_status:
        push_status(f"Transcribing audio from {name}…")
    try:
        res = transcribe_video(conv_dir / str(row["path"]))
        row["transcript"], row["transcript_note"] = res.text, res.note
    except Exception as exc:  # never block sending
        row["transcript"], row["transcript_note"] = "", f"Transcription failed: {exc}"
    return True


def external_video_hint(row: dict[str, Any], conv_dir: Path) -> str:
    hint = f"Video file: {conv_dir / str(row['path'])}"
    transcript = str(row.get("transcript") or "")
    if transcript:
        hint += f"\nTranscript of video:\n{transcript}"
    return hint


def runtime_video_dict(row: dict[str, Any], conv_dir: Path) -> dict[str, Any]:
    return {
        "kind": "video",
        "name": row.get("name") or "video",
        "mime": row.get("mime") or "",
        "abs_path": str(conv_dir / str(row["path"])),
        "transcript": str(row.get("transcript") or ""),
        "frames": [
            {"abs_path": str(conv_dir / str(f["path"])), "t_s": f.get("t_s", 0.0)}
            for f in row.get("frames") or []
            if isinstance(f, dict) and f.get("path")
        ],
    }


def backfill_history_frames(
    messages: list[dict[str, Any]],
    *,
    conv_dir: Path,
    provider: str,
    external: bool,
    push_status: Callable[[str], None] | None = None,
    model: str = "",
) -> bool:
    """Extract frames for earlier user messages whose videos this recipient can't take natively.

    Mutates rows in place; returns True when any row gained frames (caller saves the
    conversation). A failure on an old video never blocks the new turn: the row stays
    frameless and the provider builders send the "could not be analyzed" note instead.
    """
    changed = False
    frames_per_video = media_limits_for(provider, model).frames_per_video
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        for row in m.get("attachments") or []:
            if not isinstance(row, dict) or row.get("kind") != "video" or not row.get("path"):
                continue
            if not needs_frames(
                str(row.get("mime") or ""), int(row.get("size_bytes") or 0), provider=provider, external=external,
                model=model,
            ):
                continue
            if row.get("frames"):
                if _ensure_transcript(row, conv_dir, push_status):
                    changed = True
                continue
            name = row.get("name") or "video"
            if push_status:
                push_status(f"Extracting frames from {name}…")
            try:
                frames = extract_frames(conv_dir / str(row["path"]), frames_per_video)
            except VideoError as e:
                if push_status:
                    push_status(f"Could not read {name}: {e}")
                continue
            row["frames"] = [{"path": f"attachments/{f.path.name}", "t_s": f.t_s} for f in frames]
            changed = True
            _ensure_transcript(row, conv_dir, push_status)
    return changed
