"""What a plugin makes is kept in Storage, with no plugin code.

Every Store / AI plugin tool (``api.tool``) and every nested MCP server tool (Blender, Godot,
Unity, Epic, …) runs through here after it answers: each file the call created or changed
(a render, an export, a picture, a video, a sound, a 3D file, a project file) that its result
names is copied into General storage under ``<plugin label>/<YYYY-MM-DD>/``, marked with the
plugin and the tool. General storage follows the copy the app is on, so it lands in Local or
the team, synced, with history.

A team gets one change per item, so a file made again under the same name that day replaces
the one kept (its history keeps the earlier one): an export tried three times is one item,
not three. Until Nov 12 2026 each try was kept beside the others as ``name 2.ext``,
``name 3.ext``, which tripled a day of Blender exports in one team's storage.

Off for everyone with the ``keep_plugin_outputs`` setting; off for one plugin with
``"keep_outputs": false`` in its manifest. Runs on a background thread: the tool answers first.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# What counts as something made. Scripts, logs, JSON and text are a tool's working files.
KEEP_SUFFIXES = frozenset(
    {
        # pictures and design files
        ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".tga", ".exr", ".hdr",
        ".psd", ".psb", ".ai", ".eps", ".svg", ".pdf",
        # video and sound
        ".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v",
        ".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac", ".aif", ".aiff",
        # 3D
        ".fbx", ".glb", ".gltf", ".obj", ".blend", ".usd", ".usda", ".usdc", ".usdz", ".stl", ".ply",
        ".abc", ".dae", ".3mf",
        # app projects and documents
        ".aep", ".prproj", ".mogrt", ".docx", ".xlsx", ".pptx",
    }
)
MAX_BYTES = 100 * 1024 * 1024  # General storage's own per-file limit
_SLACK_SEC = 2.0  # file system time granularity
_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|/)[^\n\r\t\"'<>|*?]{1,1000}?\.[A-Za-z0-9]{1,6}(?=$|[\s\"',;)\]}])")
_RECENT: "OrderedDict[str, tuple[float, int]]" = OrderedDict()
_RECENT_LOCK = threading.Lock()
_SETTING_CACHE: dict[str, Any] = {"at": 0.0, "on": True}


def enabled() -> bool:
    """The ``keep_plugin_outputs`` setting (on unless the person turned it off), read at most every 10 s."""
    now = time.monotonic()
    if now - float(_SETTING_CACHE["at"]) < 10.0:
        return bool(_SETTING_CACHE["on"])
    on = True
    try:
        from frontend.settings import PanelSettings

        on = bool(getattr(PanelSettings.load(), "keep_plugin_outputs", True))
    except Exception:  # noqa: BLE001 — settings unreadable: keep the default
        _log.debug("keep_plugin_outputs setting unreadable", exc_info=True)
    _SETTING_CACHE.update(at=now, on=on)
    return on


def _strings(value: Any, out: list[str], depth: int = 0) -> None:
    if depth > 8 or len(out) > 500:
        return
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            _strings(v, out, depth + 1)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _strings(v, out, depth + 1)


def _as_data(result: Any) -> Any:
    if isinstance(result, (bytes, bytearray)):
        result = bytes(result).decode("utf-8", "replace")
    if isinstance(result, str):
        text = result.strip()
        if text[:1] in "{[":
            try:
                return json.loads(text)
            except ValueError:
                pass
        return result
    content = getattr(result, "content", None)  # an MCP CallToolResult
    if isinstance(content, list):
        return [str(getattr(block, "text", "") or "") for block in content]
    return result


def candidate_paths(result: Any) -> list[Path]:
    """Absolute file paths a tool result names (JSON fields at any depth, or plain text)."""
    strings: list[str] = []
    _strings(_as_data(result), strings)
    found: list[str] = []
    for text in strings:
        stripped = text.strip()
        if (len(stripped) < 1024 and "\n" not in stripped and Path(stripped).is_absolute()
                and Path(stripped).suffix.lower() in KEEP_SUFFIXES):
            found.append(stripped)
            continue
        found.extend(m.group(0).strip() for m in _PATH_RE.finditer(text))
    out: list[Path] = []
    for raw in dict.fromkeys(found):
        path = Path(raw)
        name = path.name.lower()
        if path.suffix.lower() not in KEEP_SUFFIXES and not name.endswith(".motion.html"):
            continue
        if path.is_absolute():
            out.append(path)
    return out


def made_during(path: Path, since: float) -> bool:
    try:
        st = path.stat()
    except OSError:
        return False
    return path.is_file() and 0 < st.st_size <= MAX_BYTES and st.st_mtime >= since - _SLACK_SEC


def _seen(path: Path) -> bool:
    """The same file (path, mtime, size) was already kept: a tool that names it again."""
    try:
        st = path.stat()
    except OSError:
        return True
    key = str(path.resolve()).lower()
    stamp = (st.st_mtime, st.st_size)
    with _RECENT_LOCK:
        if _RECENT.get(key) == stamp:
            return True
        _RECENT[key] = stamp
        _RECENT.move_to_end(key)
        while len(_RECENT) > 2000:
            _RECENT.popitem(last=False)
    return False


def _folder_name(label: str) -> str:
    return re.sub(r"[\\/]+", " ", str(label or "").strip()) or "Plugin"


def keep_file(path: Path, *, owner: str, label: str, tool: str, when: float | None = None) -> dict[str, Any] | None:
    """Copy one file into General storage under ``<label>/<date>``. A file made again under the
    same name that day replaces the one kept (its history keeps the earlier one); the very
    same bytes are never kept twice."""
    from backend.uefn_plugins import general_storage as gs

    data = path.read_bytes()
    digest = hashlib.sha1(data).hexdigest()
    day = time.strftime("%Y-%m-%d", time.localtime(when or time.time()))
    folder = f"{_folder_name(label)}/{day}"
    stem, suffix = path.stem, path.suffix
    if path.name.lower().endswith(".motion.html"):
        stem, suffix = path.name[: -len(".motion.html")], ".motion.html"
    name = f"{stem}{suffix}"
    existing = gs.find(name, folder)
    if existing is not None and existing.get("sha1") == digest:
        return None  # kept already
    res = gs.put_file(name, data, folder=folder, by=owner,
                      extra={"sha1": digest, "tool": tool, "source_path": str(path)})
    return res.get("item") if isinstance(res, dict) else None


def keep_outputs(owner: str, label: str, tool: str, result: Any, since: float) -> list[dict[str, Any]]:
    """Keep every file ``result`` names that the call made. Never raises."""
    kept: list[dict[str, Any]] = []
    try:
        for path in candidate_paths(result):
            if not made_during(path, since) or _seen(path):
                continue
            try:
                item = keep_file(path, owner=owner, label=label, tool=tool)
            except PermissionError:  # a read-only team copy: nothing to do here
                _log.info("Storage is read-only; %s not kept", path.name)
                continue
            except Exception:  # noqa: BLE001 — one file never stops the others
                _log.debug("keeping %s from %s failed", path, tool, exc_info=True)
                continue
            if item:
                kept.append(item)
        if kept:
            _log.info("kept %d file(s) from %s in Storage under %s", len(kept), tool, _folder_name(label))
    except Exception:  # noqa: BLE001
        _log.debug("keep_outputs failed for %s", tool, exc_info=True)
    return kept


def _manifest_allows(manifest: dict[str, Any] | None) -> bool:
    return not (isinstance(manifest, dict) and manifest.get("keep_outputs") is False)


def after_plugin_tool(plugin_id: str, tool: str, result: Any, since: float) -> None:
    """An ``api.tool`` call answered: keep what it made, in the background."""
    if not enabled():
        return
    try:
        from backend.uefn_plugins.store import load_plugin_manifest

        manifest = load_plugin_manifest(plugin_id) or {}
    except Exception:  # noqa: BLE001
        manifest = {}
    if not _manifest_allows(manifest):
        return
    label = str(manifest.get("label") or plugin_id)
    _spawn(plugin_id, label, tool, result, since)


def after_nested_tool(server_id: str, tool: str, result: Any, since: float) -> None:
    """A nested MCP server tool answered (Blender, Godot, Unity, Epic, …): the same, labelled by the server."""
    if not enabled():
        return
    try:
        from backend.mcp_plugins.store import load_plugin_manifest

        manifest = load_plugin_manifest(server_id) or {}
    except Exception:  # noqa: BLE001
        manifest = {}
    if not _manifest_allows(manifest):
        return
    label = str(manifest.get("label") or server_id)
    _spawn(server_id, label, tool, result, since)


def _spawn(owner: str, label: str, tool: str, result: Any, since: float) -> None:
    # Cheap first: no path in the answer means no thread.
    if not candidate_paths(result):
        return
    threading.Thread(
        target=keep_outputs, args=(owner, label, tool, result, since),
        name=f"keep-outputs-{owner}", daemon=True,
    ).start()
