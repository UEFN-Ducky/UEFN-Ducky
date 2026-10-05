"""Build provider-specific multimodal user content from attachments."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from backend.agent.message_attachment import MessageAttachment


def image_attachments(attachments: list[MessageAttachment]) -> list[MessageAttachment]:
    return [a for a in attachments if a.kind == "image" and a.data_base64]


def media_attachments(attachments: list[MessageAttachment]) -> list[MessageAttachment]:
    """Images with pixels plus videos with a file on disk — what providers can see."""
    return [
        a
        for a in attachments
        if (a.kind == "image" and a.data_base64) or (a.kind == "video" and a.file_path)
    ]


def _mmss(t: float) -> str:
    m, s = divmod(int(t), 60)
    return f"{m:02d}:{s:02d}"


def _video_frames(att: MessageAttachment) -> list[tuple[str, bytes]]:
    """(label, jpeg bytes) per readable frame."""
    out: list[tuple[str, bytes]] = []
    n = len(att.frames)
    for k, (path, t_s) in enumerate(att.frames, 1):
        try:
            raw = Path(path).read_bytes()
        except OSError:
            continue
        out.append((f'Video "{att.name}" — frame {k}/{n} at {_mmss(t_s)}', raw))
    return out


def _video_note(att: MessageAttachment) -> str:
    return f'[Video "{att.name}" attached but could not be analyzed]'


def _transcript_text(att: MessageAttachment) -> str:
    return f'Transcript of video "{att.name}":\n{att.transcript}'


def _omitted_note(att: MessageAttachment) -> str:
    return f'[Video "{att.name}" sent earlier — not re-attached to keep the request small]'


def build_anthropic_user_content(text: str, attachments: list[MessageAttachment]) -> str | list[dict[str, Any]]:
    media = media_attachments(attachments)
    if not media:
        return text
    blocks: list[dict[str, Any]] = []
    if text:
        blocks.append({"type": "text", "text": text})
    for att in media:
        if att.kind == "image":
            blocks.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": att.mime or "image/png", "data": att.data_base64},
                }
            )
            continue
        if att.omitted:
            blocks.append({"type": "text", "text": _omitted_note(att)})
            continue
        frames = _video_frames(att)
        if att.transcript:
            blocks.append({"type": "text", "text": _transcript_text(att)})
        if not frames:
            blocks.append({"type": "text", "text": _video_note(att)})
        for label, raw in frames:
            blocks.append({"type": "text", "text": label})
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.b64encode(raw).decode("ascii"),
                    },
                }
            )
    return blocks or text


def build_openai_user_content(text: str, attachments: list[MessageAttachment]) -> str | list[dict[str, Any]]:
    media = media_attachments(attachments)
    if not media:
        return text
    parts: list[dict[str, Any]] = []
    if text:
        parts.append({"type": "text", "text": text})
    for att in media:
        if att.kind == "image":
            mime = att.mime or "image/png"
            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{att.data_base64}"}})
            continue
        if att.omitted:
            parts.append({"type": "text", "text": _omitted_note(att)})
            continue
        frames = _video_frames(att)
        if att.transcript:
            parts.append({"type": "text", "text": _transcript_text(att)})
        if not frames:
            parts.append({"type": "text", "text": _video_note(att)})
        for label, raw in frames:
            parts.append({"type": "text", "text": label})
            b64 = base64.b64encode(raw).decode("ascii")
            parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    return parts or text


def build_gemini_user_parts(text: str, attachments: list[MessageAttachment]) -> list[Any]:
    from google.genai import types

    from backend.agent.video.routing import gemini_inline_mime

    parts: list[Any] = []
    if text:
        parts.append(types.Part.from_text(text=text))
    for att in media_attachments(attachments):
        if att.kind == "image":
            raw = base64.b64decode(att.data_base64)
            parts.append(types.Part.from_bytes(data=raw, mime_type=att.mime or "image/png"))
            continue
        if att.omitted:
            parts.append(types.Part.from_text(text=_omitted_note(att)))
            continue
        native = gemini_inline_mime(att.mime, att.size_bytes) if att.inline_ok else None
        if native:
            try:
                parts.append(types.Part.from_bytes(data=Path(att.file_path).read_bytes(), mime_type=native))
                continue
            except OSError:
                pass
        frames = _video_frames(att)
        if att.transcript:
            parts.append(types.Part.from_text(text=_transcript_text(att)))
        if not frames:
            parts.append(types.Part.from_text(text=_video_note(att)))
        for label, raw in frames:
            parts.append(types.Part.from_text(text=label))
            parts.append(types.Part.from_bytes(data=raw, mime_type="image/jpeg"))
    return parts
