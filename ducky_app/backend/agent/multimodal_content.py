"""Build provider-specific multimodal user content from attachments."""

from __future__ import annotations

import base64
from typing import Any

from backend.agent.message_attachment import MessageAttachment


def image_attachments(attachments: list[MessageAttachment]) -> list[MessageAttachment]:
    return [a for a in attachments if a.kind == "image" and a.data_base64]


def build_anthropic_user_content(text: str, attachments: list[MessageAttachment]) -> str | list[dict[str, Any]]:
    images = image_attachments(attachments)
    if not images:
        return text
    blocks: list[dict[str, Any]] = []
    if text:
        blocks.append({"type": "text", "text": text})
    for att in images:
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": att.mime or "image/png",
                    "data": att.data_base64,
                },
            }
        )
    return blocks or text


def build_openai_user_content(text: str, attachments: list[MessageAttachment]) -> str | list[dict[str, Any]]:
    images = image_attachments(attachments)
    if not images:
        return text
    parts: list[dict[str, Any]] = []
    if text:
        parts.append({"type": "text", "text": text})
    for att in images:
        mime = att.mime or "image/png"
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{att.data_base64}"},
            }
        )
    return parts or text


def build_gemini_user_parts(text: str, attachments: list[MessageAttachment]) -> list[Any]:
    from google.genai import types

    parts: list[Any] = []
    if text:
        parts.append(types.Part.from_text(text=text))
    for att in image_attachments(attachments):
        raw = base64.b64decode(att.data_base64)
        parts.append(types.Part.from_bytes(data=raw, mime_type=att.mime or "image/png"))
    return parts
