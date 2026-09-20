"""Durable generated images — AppData only, never the UEFN project, never tool_captures.

tool_captures is a 40-file screenshot prune. This folder is user data.
"""

from __future__ import annotations

import base64
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from frontend.app_paths import resolve_app_data_dir
from frontend.settings import PANEL_LISTENER_PORT

_PANEL_UI_HTTP_PORT = PANEL_LISTENER_PORT - 1
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.(?:png|jpe?g|webp)$", re.IGNORECASE)
_INDEX = "index.json"
_IMAGE_TOOLS = frozenset(
    {
        "meshy_text_to_image",
        "photoshop_generate_image",
        "openai.image",
        "google.image",
    }
)


def generated_images_dir(*, for_write: bool = False) -> Path:
    path = resolve_app_data_dir(for_write=for_write) / "generated_images"
    if for_write:
        path.mkdir(parents=True, exist_ok=True)
    return path


def build_generated_image_url(filename: str) -> str:
    return f"http://127.0.0.1:{_PANEL_UI_HTTP_PORT}/generated-images/{Path(filename).name}"


def resolve_generated_image_path(filename: str) -> Path:
    name = Path(filename or "").name
    if not _NAME_RE.match(name):
        raise ValueError("Invalid generated image name")
    target = (generated_images_dir() / name).resolve()
    root = generated_images_dir().resolve()
    target.relative_to(root)
    if not target.is_file():
        raise ValueError("Not a file")
    return target


def _index_path() -> Path:
    return generated_images_dir(for_write=True) / _INDEX


def _load_index() -> list[dict[str, Any]]:
    path = generated_images_dir() / _INDEX
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [row for row in raw if isinstance(row, dict)] if isinstance(raw, list) else []


def _save_index(rows: list[dict[str, Any]]) -> None:
    _index_path().write_text(json.dumps(rows, indent=2), encoding="utf-8")


def save_generated_image(
    raw: bytes,
    *,
    prompt: str = "",
    gateway: str = "",
    model: str = "",
    filename: str = "",
    mime: str = "image/png",
) -> dict[str, Any]:
    if not raw:
        return {"ok": False, "error": "empty image"}
    dest_dir = generated_images_dir(for_write=True)
    ext = ".jpg" if "jpeg" in mime or "jpg" in mime else ".webp" if "webp" in mime else ".png"
    name = Path(filename).name if filename else f"gen_{int(time.time())}_{uuid.uuid4().hex[:8]}{ext}"
    if not _NAME_RE.match(name):
        name = f"gen_{int(time.time())}_{uuid.uuid4().hex[:8]}{ext}"
    dest = dest_dir / name
    dest.write_bytes(raw)
    row = {
        "ok": True,
        "name": name,
        "path": str(dest),
        "filename": name,
        "media_url": build_generated_image_url(name),
        "prompt": prompt,
        "gateway": gateway,
        "model": model,
        "created": time.time(),
        "bytes": len(raw),
        "mime": mime,
    }
    rows = [r for r in _load_index() if r.get("name") != name]
    rows.insert(0, {k: v for k, v in row.items() if k != "ok"})
    _save_index(rows)
    return row


def list_generated_images() -> list[dict[str, Any]]:
    rows = _load_index()
    root = generated_images_dir()
    out: list[dict[str, Any]] = []
    for row in rows:
        name = str(row.get("name") or row.get("filename") or "")
        path = root / name
        if name and path.is_file():
            item = dict(row)
            item["path"] = str(path)
            item["media_url"] = build_generated_image_url(name)
            out.append(item)
    return out


def attachment_from_path(path: str | Path) -> dict[str, Any] | None:
    src = Path(path)
    if not src.is_file() or src.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        return None
    mime = "image/jpeg" if src.suffix.lower() in {".jpg", ".jpeg"} else "image/webp" if src.suffix.lower() == ".webp" else "image/png"
    return {
        "kind": "image",
        "name": src.name,
        "mime": mime,
        "data_base64": base64.b64encode(src.read_bytes()).decode("ascii"),
        "path": str(src),
    }


def attachments_from_tool_result(tool_name: str, data: Any) -> list[dict[str, Any]]:
    """Persist a tool's image output and return chat attachments."""
    name = str(tool_name or "")
    if name not in _IMAGE_TOOLS and not name.endswith(".image") and "_to_image" not in name and "generate_image" not in name:
        return []
    blob = data if isinstance(data, dict) else {}
    path = str(blob.get("path") or blob.get("output_path") or blob.get("file") or "")
    b64 = str(blob.get("data_base64") or blob.get("b64_json") or blob.get("image_base64") or "")
    raw = b""
    if path and Path(path).is_file():
        raw = Path(path).read_bytes()
    elif b64:
        try:
            raw = base64.b64decode(b64)
        except ValueError:
            raw = b""
    if not raw:
        return []
    saved = save_generated_image(
        raw,
        prompt=str(blob.get("prompt") or ""),
        gateway=str(blob.get("gateway") or ""),
        model=str(blob.get("model") or ""),
        filename=Path(path).name if path else "",
    )
    att = attachment_from_path(str(saved.get("path") or ""))
    return [att] if att else []
