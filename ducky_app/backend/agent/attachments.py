"""Normalize chat attachments for storage and provider dispatch."""

from __future__ import annotations

import base64
import re
from typing import Any

from backend.agent.message_attachment import MessageAttachment
from backend.agent.model_capabilities import model_in_cache, supports_vision
from backend.agent.multimodal_content import media_attachments

_MAX_IMAGE_BYTES = 20 * 1024 * 1024
# Share of the model's context window the files in one message may fill; the rest
# stays free for Ducky's instructions, the chat so far and the reply.
_FILE_SHARE_OF_CONTEXT = 0.75
_DATA_URL_RE = re.compile(r"^data:[^;]+;base64,")


def _strip_data_url_prefix(data: str) -> str:
    return _DATA_URL_RE.sub("", (data or "").strip())


def parse_attachment_dict(raw: dict[str, Any], *, current: bool = False) -> MessageAttachment | None:
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip().lower()
    name = str(raw.get("name") or "attachment").strip() or "attachment"
    mime = str(raw.get("mime") or "").strip()
    from backend.workspace.ai_ignore import current_policy

    policy = current_policy()
    protected = any(policy.denied_hint(str(raw.get(key) or name))
                    for key in ("name", "path", "project_path", "abs_path"))
    if protected:
        if current:
            raise ValueError("AI_FILE_IGNORED: this attachment is protected.")
        return None
    if kind == "image":
        data_b64 = _strip_data_url_prefix(str(raw.get("data_base64") or ""))
        if not data_b64 and raw.get("stored_conv_id"):
            from pathlib import Path
            from frontend.ui_web.conversation_attachments import resolve_chat_attachment_path

            path = str(raw.get("path") or "")
            filename = Path(path).name
            if path != f"attachments/{filename}":
                raise ValueError("Invalid attachment reference")
            full = resolve_chat_attachment_path(str(raw["stored_conv_id"]), filename)
            policy.require(str(full))
            if full.stat().st_size > _MAX_IMAGE_BYTES:
                raise ValueError(f"Image {name!r} exceeds 20MB limit")
            data_b64 = base64.b64encode(full.read_bytes()).decode("ascii")
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
        # No fixed size: the model's context window decides (check_file_text_fits).
        return MessageAttachment(kind="file", name=name, mime=mime, text=str(raw.get("text") or ""))
    if kind == "video":
        return _parse_video(raw, name, mime, current=current)
    return None


def _parse_video(raw: dict[str, Any], name: str, mime: str, *, current: bool = False) -> MessageAttachment | None:
    from backend.agent.video.limits import video_limits
    from backend.agent.video.staging import (
        VIDEO_MIME_EXT,
        normalize_video_mime,
        resolve_staged,
        safe_media_path,
    )

    gone = ValueError(f"Video {name!r} is no longer available — attach it again.")
    staged_id = str(raw.get("staged_id") or "").strip()
    if staged_id:
        path = resolve_staged(staged_id)
    else:
        abs_path = str(raw.get("abs_path") or "").strip()
        path = (
            safe_media_path(abs_path, suffixes=frozenset(VIDEO_MIME_EXT.values())) if abs_path else None
        )
        if path is None:
            if current:
                raise gone
            return None
    from backend.workspace.ai_ignore import require_ai_access

    require_ai_access(str(path))
    size = path.stat().st_size
    limit = video_limits().max_bytes
    if current and size > limit:
        raise ValueError(f"Video {name!r} exceeds the {limit // (1024 * 1024)}MB video limit")
    frames: list[tuple[str, float]] = []
    for fr in raw.get("frames") or []:
        if not isinstance(fr, dict):
            continue
        frame_abs = str(fr.get("abs_path") or "").strip()
        if not frame_abs:
            continue
        fp = safe_media_path(frame_abs, suffixes=frozenset({".jpg"}))
        if fp is not None:
            frames.append((str(fp), float(fr.get("t_s") or 0.0)))
    return MessageAttachment(
        kind="video",
        name=name,
        mime=normalize_video_mime(mime, name) or normalize_video_mime("", path.name) or "video/mp4",
        file_path=str(path),
        size_bytes=size,
        frames=frames,
        transcript=str(raw.get("transcript") or "")[:200_000],
    )


def parse_attachment_dicts(
    raw_list: list[Any] | None, *, current: bool = False, provider: str = "", model: str = "",
    enforce_image_cap: bool = True,
) -> list[MessageAttachment]:
    if not raw_list:
        return []
    from backend.agent.video.limits import media_limits_for

    max_images = media_limits_for(provider, model).max_images_per_message
    out: list[MessageAttachment] = []
    image_count = 0
    for raw in raw_list:
        att = parse_attachment_dict(raw if isinstance(raw, dict) else {}, current=current)
        if not att:
            continue
        if att.kind == "image":
            image_count += 1
            if current and enforce_image_cap and image_count > max_images:
                raise ValueError(f"At most {max_images} images per message")
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
        from frontend.ui_web.chats.store import get_conversations_dir

        raw_list = hydrate_attachment_dicts(raw_list, conv_id, get_conversations_dir(project_root), project_root)
    return parse_attachment_dicts(raw_list)


def reads_files_in_parts(provider: str, model: str, coding_agent: str = "") -> bool:
    """True when the turn can call workspace_read_file, so a big file is read in parts.

    Coding agents always have Ducky's read tools. A gateway model has them unless its API
    says it takes no tools (a hosted gateway's tool calls run on this PC too).
    """
    agent = (coding_agent or "").strip().lower()
    if agent and agent != "ducky":
        return True
    from backend.agent.model_capabilities import supports_tools
    from backend.agent.model_pricing import resolve_provider_for_model

    mid = (model or "").strip()
    prov = (resolve_provider_for_model(mid, provider) or provider or "").strip().lower()
    return not model_in_cache(prov, mid) or supports_tools(prov, mid)


def file_text_budget(provider: str, model: str, coding_agent: str = "") -> dict[str, int]:
    """How much attached text the model takes, from its context window as its API reports it.

    ``context_tokens`` is the model's whole window. ``file_inline_tokens`` is what one
    message pastes whole; a bigger file is saved and read in parts. ``file_max_tokens`` is
    the hard cap for models that cannot read in parts (0 = no cap). All are 0 when the
    model's API never reported a window: then the API itself refuses what does not fit.
    """
    from backend.agent.attached_files import inline_token_budget
    from frontend.ui_web.context_tokens import _coding_agent_context_limit, context_limit_for_model

    agent = (coding_agent or "").strip().lower()
    mid = (model or "").strip()
    if not mid:
        return {"context_tokens": 0, "file_max_tokens": 0, "file_inline_tokens": 0}
    try:
        if agent and agent != "ducky":
            ctx = _coding_agent_context_limit(agent, mid) or 0
        else:
            from backend.agent.model_pricing import resolve_provider_for_model

            ctx = context_limit_for_model(mid, resolve_provider_for_model(mid, provider)) or 0
    except Exception:
        ctx = 0
    try:
        in_parts = reads_files_in_parts(provider, mid, agent)
    except Exception:
        in_parts = False
    return {
        "context_tokens": int(ctx),
        "file_max_tokens": 0 if in_parts else int(ctx * _FILE_SHARE_OF_CONTEXT),
        "file_inline_tokens": inline_token_budget(int(ctx)) if in_parts else 0,
    }


def check_file_text_fits(
    attachments: list[MessageAttachment], *, provider: str, model: str, coding_agent: str = "",
) -> None:
    """Refuse files that would not fit in the model's context window, naming its size.

    Only for models that cannot read a saved file in parts; the rest get big files by path.
    """
    files = [a for a in attachments if a.kind == "file" and a.text]
    if not files:
        return
    budget = file_text_budget(provider, model, coding_agent)
    cap = budget["file_max_tokens"]
    if cap <= 0:
        return
    from frontend.ui_web.context_tokens import count_tokens

    total = sum(count_tokens(a.text, model, provider) for a in files)
    if total <= cap:
        return
    what = f"{files[0].name} is" if len(files) == 1 else "These files are"
    raise ValueError(
        f"{what} about {total:,} tokens. {model} holds {budget['context_tokens']:,} tokens, "
        f"so the files in one message can use about {cap:,}. "
        "Pick a model with a bigger context window, or attach less."
    )


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
    coding_agent: str = "",
    inline_files: bool = True,
) -> tuple[str, list[dict[str, Any]]]:
    """Validate attachments, inline file text, and return stored message fields.

    ``external_agent`` skips the embedded-model vision capability check: a BYOA
    coding agent (Claude Code / Codex / Cursor) handles images itself, so the
    panel's own provider/model must not gate them. ``coding_agent`` picks whose
    model catalog sizes the file limit. ``inline_files=False`` leaves file text out:
    the caller adds it with ``add_file_blocks`` once the files are saved.
    """
    attachments = parse_attachment_dicts(attachments_raw, current=True, provider=provider, model=model)
    check_file_text_fits(attachments, provider=provider, model=model, coding_agent=coding_agent)
    images = media_attachments(attachments)
    if images and not external_agent:
        if not model_in_cache(provider, model):
            raise ValueError("Model capabilities unknown — reload models in settings.")
        if not supports_vision(provider, model):
            raise ValueError(f"Model {model!r} does not support images — pick a vision model or remove images.")
    content = merge_file_text_into_content(text, attachments if inline_files else [])
    # Persist chat images under AppData tool_captures (never the UEFN project
    # folder — project-side Ducky storage is .ducky/** only).
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
        elif a.kind == "video":
            row = {"kind": "video", "name": a.name, "mime": a.mime, "size_bytes": a.size_bytes}
            if a.transcript:
                row["transcript"] = a.transcript
            stored.append(row)
    if path_hints:
        content = (content + "\n\n" if content else "") + "\n".join(path_hints)
    return content, stored


def add_file_blocks(
    content: str,
    attachments: list[MessageAttachment],
    stored: list[dict[str, Any]],
    conv_dir: Any,
    *,
    provider: str,
    model: str,
    coding_agent: str = "",
) -> str:
    """Append each attached text file to ``content``: whole when it fits, else by path.

    ``stored`` are the rows ``persist_message_attachments`` wrote, in the same order as
    the file attachments. Each row gets ``sent`` ("whole" / "parts") so the chat can say
    how the model received it.
    """
    from pathlib import Path

    from backend.agent.attached_files import file_block
    from frontend.ui_web.context_tokens import count_tokens

    files = [a for a in attachments if a.kind == "file"]
    if not any(a.text for a in files):
        return content
    rows = [r for r in stored if r.get("kind") == "file"]
    budget = file_text_budget(provider, model, coding_agent)["file_inline_tokens"]
    base = Path(conv_dir) if conv_dir else None
    blocks: list[str] = []
    for i, att in enumerate(files):
        if not att.text:
            continue
        row = rows[i] if i < len(rows) else {}
        saved = str(base / row["path"]) if base and row.get("path") else ""
        readable = str(base / row["readable_path"]) if base and row.get("readable_path") else ""
        block, sent = file_block(
            att.name, att.text, count_tokens=lambda t: count_tokens(t, model, provider),
            budget=budget, saved_path=saved, readable_path=readable,
        )
        if row:
            row["sent"] = sent
        blocks.append(block)
    return "\n\n".join([p for p in [(content or "").strip(), *blocks] if p])
