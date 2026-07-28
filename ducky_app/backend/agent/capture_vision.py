"""Attach capture-tool PNGs to the next model turn for vision."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from backend.agent.message_attachment import MessageAttachment

_CAPTURE_TOOLS = frozenset(
    {
        "take_high_res_screenshot",
        "blender_get_viewport_screenshot",
    }
)
_MAX_VISION_BYTES = 8 * 1024 * 1024
_PATH_KEYS = (
    "path",
    "ue_screenshot_path",
    "filepath",
    "preview_file",
)


def is_capture_tool(name: str) -> bool:
    return (name or "").strip() in _CAPTURE_TOOLS


def _parse_result_obj(data: Any) -> dict[str, Any] | None:
    if isinstance(data, dict):
        return data
    if not isinstance(data, str):
        return None
    text = data.strip()
    if not text.startswith("{") and not text.startswith("["):
        # Tool may return "json\nImage..." text — take first JSON object line.
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("{"):
                text = line
                break
    try:
        from backend.serialization import parse_tool_result_envelope

        obj = parse_tool_result_envelope(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _unwrap_capture_obj(obj: dict[str, Any]) -> dict[str, Any]:
    """Prefer nested capture payloads when envelopes wrap the real result."""
    data = obj.get("data")
    if isinstance(data, dict) and any(k in data for k in _PATH_KEYS):
        return data
    if isinstance(data, str) and data.strip().startswith("{"):
        try:
            inner = json.loads(data)
            if isinstance(inner, dict) and any(k in inner for k in _PATH_KEYS):
                return inner
        except Exception:
            pass
    return obj


def _pick_capture_path(obj: dict[str, Any]) -> str:
    for key in _PATH_KEYS:
        path = str(obj.get(key) or "").strip()
        if path:
            return path
    return ""


def vision_attachments_from_capture_result(
    tool_name: str,
    data: Any,
) -> list[MessageAttachment]:
    """Load project PNG from a capture tool result for multimodal user attach."""
    if not is_capture_tool(tool_name):
        return []
    obj = _parse_result_obj(data)
    if not obj:
        return []
    obj = _unwrap_capture_obj(obj)
    status = str(obj.get("status") or "").strip().lower()
    if status in ("failed", "timed_out", "pending"):
        return []
    if obj.get("ok") is False or obj.get("success") is False:
        return []
    path = _pick_capture_path(obj)
    if not path:
        return []
    src = Path(path)
    if not src.is_file():
        return []
    try:
        raw = src.read_bytes()
    except OSError:
        return []
    if not raw or len(raw) > _MAX_VISION_BYTES:
        return []
    name = src.name or "capture.png"
    return [
        MessageAttachment(
            kind="image",
            name=name,
            mime="image/png",
            data_base64=base64.b64encode(raw).decode("ascii"),
        )
    ]
