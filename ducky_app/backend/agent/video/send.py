"""Send-time video rows: a video is referenced by path, never broken down at send.

The agent looks inside on request with web_video(url=<path>) (backend/tools/core/web_video.py).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.agent.video.frames import VideoError, require_media_access


def _require_video_row(row: dict[str, Any], conv_dir: Path) -> None:
    from backend.workspace.ai_ignore import DENIED, current_policy

    try:
        if current_policy().denied_hint(str(row.get("name") or "video")):
            raise VideoError(DENIED)
        require_media_access(conv_dir / str(row["path"]))
    except ValueError as exc:
        raise VideoError(str(exc)) from exc


def external_video_hint(row: dict[str, Any], conv_dir: Path) -> str:
    _require_video_row(row, conv_dir)
    path = conv_dir / str(row["path"])
    hint = (f"Video file: {path} (referenced, not sent to you). Only if the request needs what is in "
            f'it: web_video(url="{path}", overview=8) for contact sheets, 8 frames per picture, or '
            "frames=[seconds].")
    transcript = str(row.get("transcript") or "")
    if transcript:
        hint += f"\nTranscript of video:\n{transcript}"
    return hint


def runtime_video_dict(row: dict[str, Any], conv_dir: Path) -> dict[str, Any]:
    _require_video_row(row, conv_dir)
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
