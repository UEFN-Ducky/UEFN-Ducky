"""Shared attachment dataclass for chat multimodal messages."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MessageAttachment:
    kind: str  # image | file | video
    name: str
    mime: str = ""
    data_base64: str = ""
    text: str = ""
    # video only: absolute source file, its size, and extracted (abs path, seconds) frames
    file_path: str = ""
    size_bytes: int = 0
    frames: list[tuple[str, float]] = field(default_factory=list)
    # set by video.budget.apply_media_budget: send frames instead of native bytes / skip entirely
    inline_ok: bool = True
    omitted: bool = False
    # video only: audio transcript (frame-based recipients only); "" when unavailable
    transcript: str = ""
