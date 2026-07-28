"""Normalize chat attachments for storage and provider dispatch."""

from __future__ import annotations

import base64
import re
from typing import Any

from backend.agent.message_attachment import MessageAttachment
from backend.agent.model_capabilities import model_in_cache, supports_vision
from backend.agent.multimodal_content import image_attachments

_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_MAX_IMAGES = 4
_MAX_FILE_TEXT = 256 * 1024
_DATA_URL_RE = re.compile(r"^data:[^;]+;base64,")


def _strip_data_url_prefix(data: str) -> str:
    return _DATA_URL_RE.sub("", (data or "").strip())


def parse_attachment_dict(raw: dict[str, Any]) -> MessageAttachment | None:
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip().lower()
    name = str(raw.get("name") or "attachment").strip() or "attachment"
    mime = str(raw.get("mime") or "").strip()
    if kind == "image":
        data_b64 = _strip_data_url_prefix(str(raw.get("data_base64") or ""))
        if not data_b64:
            return None
        try:
            raw_bytes = base64.b64decode(data_b64, validate=True)
        except Exception:
            return None
        if len(raw_bytes) > _MAX_IMAGE_BYTES:
            raise ValueError(f"Image {name!r} exceeds 20MB limit")
        if not mime.startswith("image/"):
            mime = "image/png"
        return MessageAttachment(kind="image", name=name, mime=mime, data_base64=data_b64)
    if kind == "file":
        text = str(raw.get("text") or "")
        if len(text.encode("utf-8")) > _MAX_FILE_TEXT:
            raise ValueError(f"File {name!r} exceeds 256KB text limit")
        return MessageAttachment(kind="file", name=name, mime=mime, text=text)
    return None


def parse_attachment_dicts(raw_list: list[Any] | None) -> list[MessageAttachment]:
    if not raw_list:
        return []
    out: list[MessageAttachment] = []
    image_count = 0
    for raw in raw_list:
        att = parse_attachment_dict(raw if isinstance(raw, dict) else {})
        if not att:
            continue
        if att.kind == "image":
            image_count += 1
            if image_count > _MAX_IMAGES:
                raise ValueError(f"At most {_MAX_IMAGES} images per message")
        out.append(att)
    return out


def attachments_from_message_dict(
    message: dict[str, Any],
    *,
    conv_id: str | None = None,
    project_root: str | None = None,
) -> list[MessageAttachment]:
    raw_list = message.get("attachments")
    if raw_list and conv_id:
        from frontend.ui_web.conversation_attachments import hydrate_attachment_dicts
        from frontend.ui_web.project_chats import get_conversations_dir

        raw_list = hydrate_attachment_dicts(raw_list, conv_id, get_conversations_dir(project_root), project_root)
    return parse_attachment_dicts(raw_list)


def merge_file_text_into_content(text: str, attachments: list[MessageAttachment]) -> str:
    parts: list[str] = []
    base = (text or "").strip()
    if base:
        parts.append(base)
    for att in attachments:
        if att.kind != "file" or not att.text:
            continue
        parts.append(f"Attached file: {att.name}\n```\n{att.text}\n```")
    return "\n\n".join(parts)


def prepare_outgoing_user_message(
    text: str,
    attachments_raw: list[Any] | None,
    *,
    provider: str,
    model: str,
    external_agent: bool = False,
) -> tuple[str, list[dict[str, Any]]]:
    """Validate attachments, inline file text, and return stored message fields.

    ``external_agent`` skips the embedded-model vision capability check: a BYOA
    coding agent (Claude Code / Codex / Cursor) handles images itself, so the
    panel's own provider/model must not gate them.
    """
    attachments = parse_attachment_dicts(attachments_raw)
    images = image_attachments(attachments)
    if images and not external_agent:
        if not model_in_cache(provider, model):
            raise ValueError("Model capabilities unknown — reload models in settings.")
        if not supports_vision(provider, model):
            raise ValueError(f"Model {model!r} does not support images — pick a vision model or remove images.")
    content = merge_file_text_into_content(text, attachments)
    # Mirror chat images into project Saved/DuckyCaptures so agents get a
    # workspace-reachable path (AppData attachments stay preview-only).
    existing_paths: dict[str, str] = {}
    for raw in attachments_raw or []:
        if not isinstance(raw, dict) or str(raw.get("kind") or "") != "image":
            continue
        name = str(raw.get("name") or "").strip()
        p = str(raw.get("project_path") or "").strip()
        if name and p:
            existing_paths[name] = p
    path_hints: list[str] = []
    stored: list[dict[str, Any]] = []
    for a in attachments:
        if a.kind == "image":
            row: dict[str, Any] = {
                "kind": "image",
                "name": a.name,
                "mime": a.mime,
                "data_base64": a.data_base64,
            }
            project_path = existing_paths.get(a.name, "")
            if not project_path:
                try:
                    from frontend.ui_web.tool_captures import copy_png_to_ducky_captures

                    raw = base64.b64decode(a.data_base64, validate=True)
                    project_path = copy_png_to_ducky_captures(
                        raw, prefix="chat", filename=a.name
                    )
                except Exception:
                    project_path = ""
            if project_path:
                row["project_path"] = project_path
                path_hints.append(f"Capture file: {project_path}")
            stored.append(row)
        elif a.kind == "file":
            stored.append(
                {
                    "kind": "file",
                    "name": a.name,
                    "mime": a.mime,
                    "text": a.text,
                }
            )
    if path_hints:
        content = (content + "\n\n" if content else "") + "\n".join(path_hints)
    return content, stored
