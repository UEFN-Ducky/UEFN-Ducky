"""Decide whether a recipient gets the raw video (Gemini) or extracted frames."""

from __future__ import annotations

GEMINI_PROVIDER = "gemini"
GEMINI_INLINE_MAX_BYTES = 20 * 1024 * 1024
_GEMINI_MIME = {
    "video/mp4": "video/mp4",
    "video/webm": "video/webm",
    "video/quicktime": "video/mov",
}


def gemini_inline_mime(mime: str, size_bytes: int) -> str | None:
    if size_bytes > GEMINI_INLINE_MAX_BYTES:
        return None
    return _GEMINI_MIME.get((mime or "").strip().lower())


def model_accepts_video(provider: str, model: str = "") -> bool:
    """False only when the model is known to not take video input."""
    from backend.agent.model_fetch import get_model_info

    info = get_model_info(provider, model)
    return getattr(info, "supports_video", None) is not False


def needs_frames(mime: str, size_bytes: int, *, provider: str, external: bool, model: str = "") -> bool:
    if external:
        return True
    if (provider or "").strip().lower() != GEMINI_PROVIDER:
        return True
    if gemini_inline_mime(mime, size_bytes) is None:
        return True
    return not model_accepts_video(provider, model)
