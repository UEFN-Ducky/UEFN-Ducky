"""Durable generated media (image, video, audio, motion scene) — AppData only, never the UEFN project, never tool_captures.

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
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.(?:png|jpe?g|webp|mp4|webm|mov|mp3|wav|ogg|m4a|html)$", re.IGNORECASE)
_INDEX = "index.json"
# Image tools that predate plugin-declared generators; the rest come from what the
# plugins turned on here declare (contributes.automations.image_generators).
_IMAGE_TOOLS = frozenset(
    {
        "meshy_text_to_image",
        "photoshop_generate_image",
        "openai.image",
        "google.image",
    }
)
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
# One folder, one URL; the file's suffix says what it is. A motion scene is a self-contained
# HTML file whose render(t) draws frame t (played in a sandboxed iframe, never as a page).
_KIND_BY_SUFFIX = {
    **{s: "image" for s in _IMAGE_SUFFIXES},
    **{s: "video" for s in (".mp4", ".webm", ".mov")},
    **{s: "audio" for s in (".mp3", ".wav", ".ogg", ".m4a")},
    ".html": "motion",
}
_EXT_BY_MIME = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/webp": ".webp", "image/png": ".png",
    "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov",
    "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav",
    "audio/ogg": ".ogg", "audio/mp4": ".m4a", "text/html": ".html",
}


def media_kind(name: str) -> str:
    """image | video | audio | motion for a stored file name, \"\" when it is none of them."""
    return _KIND_BY_SUFFIX.get(Path(name or "").suffix.lower(), "")


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
    conv_id: str = "",
) -> dict[str, Any]:
    if not raw:
        return {"ok": False, "error": "empty image"}
    dest_dir = generated_images_dir(for_write=True)
    ext = _EXT_BY_MIME.get(mime.split(";")[0].strip().lower(), ".png")
    name = Path(filename).name if filename else f"gen_{int(time.time())}_{uuid.uuid4().hex[:8]}{ext}"
    if not _NAME_RE.match(name):
        name = f"gen_{int(time.time())}_{uuid.uuid4().hex[:8]}{ext}"
    if (dest_dir / name).exists():
        # Another picture (maybe another chat's) has this name: never take over its row.
        name = f"{Path(name).stem}_{uuid.uuid4().hex[:8]}{Path(name).suffix}"
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
        "kind": media_kind(name),
        "conv_id": conv_id,
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


def image_tool_names() -> set[str]:
    names = set(_IMAGE_TOOLS)
    try:
        from backend.uefn_plugins.plugin_host.ui_registry import image_generator_tools

        names |= image_generator_tools()
    except Exception:
        pass
    return names


def is_image_tool(tool_name: str) -> bool:
    name = str(tool_name or "")
    return name in image_tool_names() or name.endswith(".image") or "_to_image" in name or "generate_image" in name


def _mime_for(path: Path) -> str:
    from frontend.ui_web.file_kinds import mime_for_path

    return mime_for_path(path.name)


def _as_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return {}
    if not isinstance(result, dict):
        return {}
    if isinstance(result.get("data"), dict):
        return {**result["data"], **{k: v for k, v in result.items() if k != "data"}}
    return result


def _paths_in(data: dict[str, Any]) -> list[str]:
    """File paths a tool result names: ``downloaded: [path, …]``, ``path`` and friends, ``media: {path}``."""
    paths: list[str] = []
    for item in data.get("downloaded") or []:
        if isinstance(item, dict):
            item = item.get("path") or item.get("file") or ""
        paths.append(str(item or ""))
    paths += [str(data.get(key) or "") for key in ("path", "output_path", "file", "saved_path")]
    if isinstance(data.get("media"), dict):
        paths.append(str(data["media"].get("path") or ""))
    return list(dict.fromkeys(p for p in paths if p))


def _media_paths_in(result: Any) -> list[Path]:
    """Videos, sounds and motion scenes a tool result names. Pictures stay with image tools;
    an .html only counts as ``*.motion.html`` (any tool may return a report page)."""
    out = []
    for raw_path in _paths_in(_as_dict(result)):
        path = Path(raw_path)
        kind = media_kind(path.name)
        if kind in ("video", "audio") or (kind == "motion" and path.name.lower().endswith(".motion.html")):
            out.append(path)
    return out


def _images_in(result: Any) -> list[tuple[str, bytes, str]]:
    """(file name, bytes, mime) for each picture a tool result names or carries.

    Plugin image tools return JSON text with ``downloaded: [path, …]``; others a ``path``
    or base64 bytes, sometimes under ``data``.
    """
    data = _as_dict(result)
    out: list[tuple[str, bytes, str]] = []
    for raw_path in _paths_in(data):
        path = Path(raw_path)
        if path.suffix.lower() in _IMAGE_SUFFIXES and path.is_file():
            out.append((path.name, path.read_bytes(), _mime_for(path)))
    b64 = str(data.get("data_base64") or data.get("b64_json") or data.get("image_base64") or "")
    if b64 and not out:
        try:
            out.append(("", base64.b64decode(b64), str(data.get("mime") or "image/png")))
        except ValueError:
            pass
    return out


def _chat_of_caller() -> str:
    try:
        from backend.workspace.identity import resolve_context

        ctx = resolve_context()
    except Exception:
        return ""
    return str(getattr(ctx, "conv_id", "") or "") if ctx is not None else ""


def note_tool_images(tool_name: str, result: Any, *, conv_id: str = "") -> list[dict[str, Any]]:
    """Keep the media a tool made in a chat, so that turn's reply shows it as a card.

    Runs for every plugin tool and every agent (Ducky, Codex, Claude Code, Cursor). Pictures
    come from image tools; a video, sound or motion scene from any tool. Only calls from a
    chat save anything: a workflow keeps its own outputs.
    """
    image_tool = is_image_tool(tool_name)
    media = _media_paths_in(result)
    if not image_tool and not media:
        return []
    chat = conv_id or _chat_of_caller()
    if not chat:
        return []
    meta = _as_dict(result)
    prompt = str(meta.get("prompt") or meta.get("revised_prompt") or "")
    kept = [row for row in (note_media_file(p, conv_id=chat, gateway=str(tool_name), prompt=prompt) for p in media) if row]
    if not image_tool:
        return kept
    return kept + [
        save_generated_image(
            raw,
            prompt=prompt,
            gateway=str(tool_name),
            model=str(meta.get("model") or ""),
            filename=name,
            mime=mime,
            conv_id=chat,
        )
        for name, raw, mime in _images_in(result)
    ]


def note_media_file(path: str | Path, *, conv_id: str, gateway: str, prompt: str = "") -> dict[str, Any] | None:
    """Keep a picture, video, sound or motion scene a tool or agent made for its chat."""
    src = Path(path)
    if not conv_id or not media_kind(src.name) or not src.is_file():
        return None
    return save_generated_image(src.read_bytes(), prompt=prompt, gateway=gateway, filename=src.name,
                                mime=_mime_for(src), conv_id=conv_id)


def note_image_file(path: str | Path, *, conv_id: str, gateway: str, prompt: str = "") -> dict[str, Any] | None:
    """Keep a picture an agent made by itself (Codex image generation) for its chat."""
    if Path(path).suffix.lower() not in _IMAGE_SUFFIXES:
        return None
    return note_media_file(path, conv_id=conv_id, gateway=gateway, prompt=prompt)


def turn_attachments(conv_id: str, since: float) -> list[dict[str, Any]]:
    """The pictures made for this chat since its turn started, oldest first.

    Stored on the reply as references (``generated``: the file name here); the chat adds
    the bytes when it loads, like the person's own attachments, so chats stay small.
    """
    if not conv_id:
        return []
    rows = [r for r in _load_index() if r.get("conv_id") == conv_id and float(r.get("created") or 0) >= since]
    root = generated_images_dir()
    out = []
    for row in sorted(rows, key=lambda r: float(r.get("created") or 0)):
        name = str(row.get("name") or "")
        if name and (root / name).is_file():
            kind = media_kind(name)
            att = {"kind": kind, "name": name, "mime": str(row.get("mime") or "image/png"), "generated": name}
            if kind != "image":
                att["path"] = str(root / name)  # the card's Open in Media Kit / Export use the file
            out.append(att)
    return out


def hydrate_generated(raw: dict[str, Any]) -> dict[str, Any]:
    """A reply's media reference for the chat to draw: a picture with its bytes, the rest by URL."""
    try:
        path = resolve_generated_image_path(str(raw.get("generated") or ""))
    except (ValueError, OSError):
        return raw
    if media_kind(path.name) != "image":
        # ponytail: videos are never base64'd into the chat; the panel server streams them.
        return {**raw, "path": str(path), "media_url": build_generated_image_url(path.name)}
    return {**raw, "data_base64": base64.b64encode(path.read_bytes()).decode("ascii")}
