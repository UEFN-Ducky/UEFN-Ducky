"""Loopback media URLs for in-panel image/model tabs (avoid pywebview JSON base64)."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote, unquote

from frontend.settings import PANEL_LISTENER_PORT
from frontend.ui_web.file_kinds import (
    classify_project_file,
    is_audio_file_name,
    is_image_file_name,
    is_model_file_name,
    is_video_file_name,
    mime_for_path,
)

_PANEL_UI_HTTP_PORT = PANEL_LISTENER_PORT - 1
_PROJECT_MEDIA_RE = re.compile(r"^project-media/(.+)$")
# Directory-relative model serving so FBX/glTF can load sibling textures/bins.
# model-files/<urlquote(encoded_dir)>/<urlquote(filename)>
_MODEL_FILES_RE = re.compile(r"^model-files/([^/]+)/(.+)$")


def project_media_re() -> re.Pattern[str]:
    return _PROJECT_MEDIA_RE


def model_files_re() -> re.Pattern[str]:
    return _MODEL_FILES_RE


def build_project_media_url(encoded_path: str) -> str:
    token = quote(encoded_path, safe="")
    return f"http://127.0.0.1:{_PANEL_UI_HTTP_PORT}/project-media/{token}"


def _split_encoded_path(encoded_path: str) -> tuple[str, str]:
    """Return (encoded_parent_dir, filename) for Content / abs: / ext: paths."""
    norm = (encoded_path or "").strip().replace("\\", "/")
    lower = norm.lower()
    if lower.startswith("ext:") or lower.startswith("abs:"):
        prefix = norm[:4]
        rest = norm[4:]
        slash = rest.rfind("/")
        if slash < 0:
            raise ValueError("Model path has no directory")
        return f"{prefix}{rest[:slash]}", rest[slash + 1 :]
    slash = norm.rfind("/")
    if slash < 0:
        raise ValueError("Model path has no directory")
    return norm[:slash], norm[slash + 1 :]


def build_model_media_urls(encoded_path: str) -> dict[str, str]:
    """URLs for Three.js: file URL + directory base for relative resource loads."""
    encoded_dir, filename = _split_encoded_path(encoded_path)
    dir_token = quote(encoded_dir, safe="")
    name_token = quote(filename, safe="")
    base = f"http://127.0.0.1:{_PANEL_UI_HTTP_PORT}/model-files/{dir_token}/"
    return {
        "media_url": f"{base}{name_token}",
        "media_base_url": base,
        "media_filename": filename,
    }


def _sandbox_resolve(encoded: str) -> Path:
    from frontend.ui_web import project_files as pf

    target = pf._resolve_file_path(encoded)  # noqa: SLF001
    if pf._is_ext_encoded_path(encoded):  # noqa: SLF001
        pass
    elif pf._is_abs_encoded_path(encoded):  # noqa: SLF001
        if not pf._is_under_any_workspace_folder(target):  # noqa: SLF001
            raise ValueError("Path escapes workspace folders")
    else:
        pf._require_under_content(target, encoded)  # noqa: SLF001
    return target


def resolve_project_media_path(token: str) -> Path:
    """Map a /project-media/<token> request to an on-disk image file."""
    encoded = unquote(token or "")
    if not encoded.strip():
        raise ValueError("Empty media token")
    target = _sandbox_resolve(encoded)
    if not target.is_file():
        raise ValueError("Not a file")
    info = classify_project_file(encoded)
    if info["kind"] not in {"image", "model", "audio", "video"} and not (
        is_image_file_name(target.name)
        or is_model_file_name(target.name)
        or is_audio_file_name(target.name)
        or is_video_file_name(target.name)
    ):
        raise ValueError("Not a media file")
    return target


def resolve_model_files_path(dir_token: str, name_token: str) -> Path:
    """Map /model-files/<dir>/<name> to a file under the sandboxed directory."""
    encoded_dir = unquote(dir_token or "").rstrip("/")
    filename = unquote(name_token or "")
    if not encoded_dir or not filename or "/" in filename or "\\" in filename or filename in {".", ".."}:
        raise ValueError("Invalid model file path")
    joined = f"{encoded_dir}/{filename}"
    target = _sandbox_resolve(joined)
    if not target.is_file():
        raise ValueError("Not a file")
    # Ensure we didn't escape the intended directory via .. in the encoded_dir itself
    # (filename is already sanitized).
    parent = _sandbox_resolve(f"{encoded_dir}/__ducky_dir_probe__").parent
    try:
        target.resolve().relative_to(parent.resolve())
    except ValueError as exc:
        raise ValueError("Path escapes model directory") from exc
    return target

def media_content_type(path: Path) -> str:
    return mime_for_path(path.name)
