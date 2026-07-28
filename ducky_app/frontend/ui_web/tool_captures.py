"""Loopback URLs for tool screenshots — keep base64 out of MCP / agent context."""

from __future__ import annotations

import re
import time
import uuid
from pathlib import Path

from frontend.app_paths import resolve_app_data_dir
from frontend.settings import PANEL_LISTENER_PORT

_PANEL_UI_HTTP_PORT = PANEL_LISTENER_PORT - 1
_TOOL_CAPTURE_RE = re.compile(r"^tool-captures/([A-Za-z0-9._-]+\.(?:png|jpe?g|webp))$", re.IGNORECASE)
_MAX_KEEP = 40


def tool_captures_dir(*, for_write: bool = False) -> Path:
    path = resolve_app_data_dir(for_write=for_write) / "tool_captures"
    if for_write:
        path.mkdir(parents=True, exist_ok=True)
    return path


def tool_captures_re() -> re.Pattern[str]:
    return _TOOL_CAPTURE_RE


def build_tool_capture_url(filename: str) -> str:
    name = Path(filename).name
    return f"http://127.0.0.1:{_PANEL_UI_HTTP_PORT}/tool-captures/{name}"


def resolve_tool_capture_path(filename: str) -> Path:
    name = Path(filename or "").name
    if not name or name != (filename or "").replace("\\", "/").rsplit("/", 1)[-1]:
        raise ValueError("Invalid capture name")
    if not _TOOL_CAPTURE_RE.match(f"tool-captures/{name}"):
        raise ValueError("Invalid capture name")
    target = (tool_captures_dir() / name).resolve()
    root = tool_captures_dir().resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("Path escapes tool captures") from exc
    if not target.is_file():
        raise ValueError("Not a file")
    return target


def _prune_old_captures(directory: Path) -> None:
    files = sorted(directory.glob("*.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in files[_MAX_KEEP:]:
        try:
            stale.unlink()
        except OSError:
            pass


def _safe_capture_prefix(prefix: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", (prefix or "capture").strip())[:40] or "capture"


def copy_png_to_ducky_captures(raw: bytes, *, prefix: str = "capture", filename: str = "") -> str:
    """Write PNG under active project's Saved/DuckyCaptures. Returns path or ''."""
    if not raw:
        return ""
    try:
        from frontend.settings import PanelSettings

        root = (PanelSettings.load().uefn_project_root or "").strip()
    except Exception:
        root = ""
    if not root:
        return ""
    try:
        dest_dir = Path(root) / "Saved" / "DuckyCaptures"
        dest_dir.mkdir(parents=True, exist_ok=True)
        if filename:
            name = Path(filename).name
        else:
            name = f"{_safe_capture_prefix(prefix)}_{int(time.time())}_{uuid.uuid4().hex[:8]}.png"
        dest = dest_dir / name
        dest.write_bytes(raw)
        return str(dest)
    except Exception:
        return ""


def save_tool_capture_png(raw: bytes, *, prefix: str = "capture") -> dict[str, object]:
    """Persist PNG bytes under AppData; return short metadata (no base64)."""
    directory = tool_captures_dir(for_write=True)
    safe_prefix = _safe_capture_prefix(prefix)
    name = f"{safe_prefix}_{int(time.time())}_{uuid.uuid4().hex[:8]}.png"
    path = directory / name
    path.write_bytes(raw)
    _prune_old_captures(directory)
    return {
        "path": str(path),
        "filename": name,
        "media_url": build_tool_capture_url(name),
        "bytes": len(raw),
        "format": "png",
    }


def save_capture_for_agents(raw: bytes, *, prefix: str = "capture") -> dict[str, object]:
    """AppData preview + project DuckyCaptures copy.

    ``path`` is the project file when available (agents use this). ``capture_path``
    / ``media_url`` are AppData preview-only for the panel UI.
    """
    saved = save_tool_capture_png(raw, prefix=prefix)
    project_path = copy_png_to_ducky_captures(
        raw, prefix=prefix, filename=str(saved.get("filename") or "")
    )
    out = dict(saved)
    out["capture_path"] = str(saved.get("path") or "")
    if project_path:
        out["path"] = project_path
    return out
