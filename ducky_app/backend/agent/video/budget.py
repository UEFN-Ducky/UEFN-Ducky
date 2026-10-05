"""Request-wide media budget: history replays every earlier video, so cap the total."""

from __future__ import annotations

from backend.agent.message_attachment import MessageAttachment
from backend.agent.video.limits import request_image_max
from backend.agent.video.routing import GEMINI_PROVIDER, gemini_inline_mime, model_accepts_video

GEMINI_INLINE_BUDGET = 14 * 1024 * 1024  # Gemini caps inline request payloads at ~20 MB total


def apply_media_budget(per_message: list[list[MessageAttachment]], *, provider: str, model: str = "") -> None:
    """Mark older videos as frames-only (``inline_ok=False``) or ``omitted`` to fit the budget.

    ``per_message`` holds each user message's attachments, oldest first. Walking newest to
    oldest lets the message being sent claim budget first. Images are never altered.
    """
    gemini = (provider or "").strip().lower() == GEMINI_PROVIDER and model_accepts_video(provider, model)
    image_budget = request_image_max(provider, model)
    images = 0
    inline_bytes = 0
    for atts in reversed(per_message):
        for att in atts:
            if att.kind == "image" and att.data_base64:
                images += 1
        for att in atts:
            if att.kind != "video" or not att.file_path:
                continue
            if (
                gemini
                and gemini_inline_mime(att.mime, att.size_bytes)
                and inline_bytes + att.size_bytes <= GEMINI_INLINE_BUDGET
            ):
                att.inline_ok = True
                inline_bytes += att.size_bytes
                continue
            att.inline_ok = False
            if att.frames and images + len(att.frames) <= image_budget:
                images += len(att.frames)
            else:
                att.omitted = True
