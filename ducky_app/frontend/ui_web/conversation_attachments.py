"""Persist and hydrate per-conversation message attachments on disk."""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

from backend.agent.message_attachment import MessageAttachment

_UNSAFE_CHARS = re.compile(r"[^\w.\-]+")


def _safe_attachment_filename(name: str) -> str:
    base = Path(name).name or "attachment"
    safe = _UNSAFE_CHARS.sub("_", base).strip("._")
    return (safe or "attachment")[:120]


def conversation_dir(conv_id: str, project_root: str | None, conversations_dir: Path) -> Path:
    return conversations_dir / conv_id


def conversation_attachments_dir(conv_id: str, project_root: str | None, conversations_dir: Path) -> Path:
    return conversation_dir(conv_id, project_root, conversations_dir) / "attachments"


def conversation_meta_path(conv_id: str, conversations_dir: Path) -> Path:
    return conversations_dir / conv_id / "conversation.json"


def persist_message_attachments(
    conv_id: str,
    message_ts: float,
    attachments: list[MessageAttachment],
    conversations_dir: Path,
    project_root: str | None = None,
) -> list[dict[str, Any]]:
    """Write attachment bytes/text under conversations/{conv_id}/attachments/."""
    del project_root  # reserved for future multi-root layouts
    if not attachments:
        return []
    conv_path = conversation_dir(conv_id, None, conversations_dir)
    att_dir = conversation_attachments_dir(conv_id, None, conversations_dir)
    att_dir.mkdir(parents=True, exist_ok=True)
    out: list[dict[str, Any]] = []
    ts_tag = int(message_ts * 1000)
    for i, att in enumerate(attachments):
        safe = _safe_attachment_filename(att.name)
        filename = f"{ts_tag}_{i}_{safe}"
        rel = f"attachments/{filename}"
        full = conv_path / rel
        if att.kind == "image" and att.data_base64:
            try:
                full.write_bytes(base64.b64decode(att.data_base64, validate=True))
            except Exception:
                continue
            out.append({"kind": "image", "name": att.name, "mime": att.mime or "image/png", "path": rel})
        elif att.kind == "file":
            full.write_text(att.text or "", encoding="utf-8")
            out.append(
                {
                    "kind": "file",
                    "name": att.name,
                    "mime": att.mime or "text/plain",
                    "path": rel,
                }
            )
    return out


def hydrate_attachment_dict(
    raw: dict[str, Any],
    conv_id: str,
    conversations_dir: Path,
    project_root: str | None = None,
) -> dict[str, Any]:
    del project_root
    if not isinstance(raw, dict):
        return raw
    kind = str(raw.get("kind") or "").strip().lower()
    path = str(raw.get("path") or "").strip()
    if not path:
        return raw
    full = conversation_dir(conv_id, None, conversations_dir) / path
    if not full.is_file():
        return raw
    if kind == "image":
        data = base64.b64encode(full.read_bytes()).decode("ascii")
        return {**raw, "data_base64": data}
    if kind == "file":
        return {**raw, "text": full.read_text(encoding="utf-8")}
    return raw


def hydrate_attachment_dicts(
    raw_list: list[Any] | None,
    conv_id: str,
    conversations_dir: Path,
    project_root: str | None = None,
) -> list[dict[str, Any]]:
    if not raw_list:
        return []
    return [
        hydrate_attachment_dict(raw if isinstance(raw, dict) else {}, conv_id, conversations_dir, project_root)
        for raw in raw_list
    ]
