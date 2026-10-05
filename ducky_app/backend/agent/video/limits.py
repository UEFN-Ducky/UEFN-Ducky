"""User-tunable video limits (Settings → Videos), clamped to safe ranges."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

VIDEO_MAX_MB_RANGE = (10, 200)
FRAMES_PER_VIDEO_RANGE = (1, 40)
MAX_IMAGES_PER_MESSAGE_RANGE = (1, 100)
DEFAULT_VIDEO_MAX_MB = 100
DEFAULT_FRAMES_PER_VIDEO = 20
DEFAULT_MAX_IMAGES_PER_MESSAGE = 40


def clamp(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


@dataclass(frozen=True)
class VideoLimits:
    max_bytes: int
    frames_per_video: int
    max_images_per_message: int


PROVIDER_IMAGE_MAX = {"anthropic": 100, "openai": 500, "gemini": 3000}
DEFAULT_IMAGE_MAX = 20
AUTO_FRAMES_CAP = 20


def clamp_auto(value: Any, lo: int, hi: int, default: int) -> int:
    """Like ``clamp`` but 0 is kept (0 = Auto)."""
    try:
        if int(value) == 0:
            return 0
    except (TypeError, ValueError):
        return default
    return clamp(value, lo, hi, default)


def request_image_max(provider: str, model: str) -> int:
    from backend.agent.model_fetch import get_model_info

    info = get_model_info(provider, model)
    cap = getattr(info, "max_images", None)
    if isinstance(cap, int) and cap > 0:
        return cap
    return PROVIDER_IMAGE_MAX.get((provider or "").strip().lower(), DEFAULT_IMAGE_MAX)


def media_limits_for(provider: str, model: str) -> VideoLimits:
    from frontend.settings import PanelSettings

    s = PanelSettings.load()
    mb = clamp(getattr(s, "video_max_mb", None), *VIDEO_MAX_MB_RANGE, DEFAULT_VIDEO_MAX_MB)
    frames = clamp_auto(getattr(s, "video_frames_per_video", None), *FRAMES_PER_VIDEO_RANGE, 0)
    images = clamp_auto(getattr(s, "max_images_per_message", None), *MAX_IMAGES_PER_MESSAGE_RANGE, 0)
    rmax = request_image_max(provider, model)
    frames = min(AUTO_FRAMES_CAP, rmax) if frames == 0 else min(frames, rmax)
    images = rmax if images == 0 else min(images, rmax)
    return VideoLimits(max_bytes=mb * 1024 * 1024, frames_per_video=frames, max_images_per_message=images)


def video_limits() -> VideoLimits:
    """Provider-agnostic limits (Auto resolves as an unknown provider)."""
    return media_limits_for("", "")
