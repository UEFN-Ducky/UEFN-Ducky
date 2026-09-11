"""Editor control registry tools: console, PIE, screenshots, saving, properties."""

from __future__ import annotations

import base64
import json
import os
import tempfile
import time
import uuid
from typing import Any

import unreal

from listener.dispatch import register
from listener.serialize import serialize


def _editor_world():
    return unreal.EditorLevelLibrary.get_editor_world()


#: Epic's in-process toolset API. ``CaptureViewport`` reads back the level
#: viewport and hands us the PNG directly — no file hunt, no console command,
#: and it completes on the calling frame.
_CAPTURE_TOOLSET = "EditorToolset.EditorAppToolset"
_CAPTURE_TOOL = "CaptureViewport"


def _captures_dir() -> str:
    """OS temp — never the UEFN project folder. The host copies into AppData."""
    root = os.path.join(tempfile.gettempdir(), "ducky_captures")
    os.makedirs(root, exist_ok=True)
    return root


def _capture_viewport_png() -> bytes:
    """PNG bytes of the active level viewport, or raise with why it failed."""
    registry = getattr(unreal, "ToolsetRegistry", None)
    if registry is None or not registry.is_available():
        raise RuntimeError(
            "Viewport capture unavailable: Epic's ToolsetRegistry is not registered "
            "in this UEFN build (EditorToolset plugin missing or not loaded yet)"
        )
    result = registry.execute_tool(_CAPTURE_TOOLSET, _CAPTURE_TOOL, "{}")
    # The capture completes on the calling frame. If it ever does not, say so —
    # waiting here would block the tick that has to finish it.
    if not result.is_complete:
        raise RuntimeError("Viewport capture did not complete on this frame")
    error = str(getattr(result, "error", "") or "")
    if error:
        raise RuntimeError(f"Viewport capture failed: {error}")
    try:
        payload = json.loads(str(result.value or "{}"))
    except (TypeError, ValueError) as e:
        raise RuntimeError(f"Viewport capture returned unreadable JSON: {e}") from e
    image = ((payload.get("returnValue") or {}).get("image") or {})
    data = str(image.get("data") or "")
    if not data:
        raise RuntimeError("Viewport capture returned an empty image")
    return base64.b64decode(data)


def _capture_filename(filename: str) -> str:
    """Unique PNG name, seeded from the caller's name when it gave one."""
    raw = os.path.basename((filename or "").strip())
    stem = raw[:-4] if raw.lower().endswith(".png") and raw.lower() != ".png" else ""
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in stem)[:48] or "uefn_ducky"
    return f"{safe}_{int(time.time())}_{uuid.uuid4().hex[:8]}.png"


def exec_console_command(command: str) -> dict:
    """Run an editor console command (e.g. 'stat fps', 'r.ScreenPercentage 100')."""
    if not command.strip():
        raise ValueError("command must not be empty")
    world = _editor_world()
    unreal.SystemLibrary.execute_console_command(world, command)
    return {"command": command, "executed": True}


def save_all_dirty(content: bool = True, maps: bool = True) -> dict:
    """Save all dirty content packages and/or maps without prompting."""
    ok = unreal.EditorLoadingAndSavingUtils.save_dirty_packages(
        save_map_packages=bool(maps), save_content_packages=bool(content)
    )
    return {"saved": bool(ok), "content": content, "maps": maps}


def take_high_res_screenshot(width: int = 1280, height: int = 720, filename: str = "") -> dict:
    """Capture the active viewport without freezing the editor.

    **Never** uses ``AutomationLibrary`` / ``HighResShot`` — those do a
    synchronous offscreen render on the Slate tick and can freeze UEFN for tens
    of seconds on dense levels. **Never** uses the ``Shot`` console command
    either: that one is a *game* viewport command and writes nothing at all in
    the editor, which is how captures used to "start" and never produce a PNG.

    Goes through Epic's ``EditorAppToolset.CaptureViewport``, which reads the
    viewport back on the calling frame and returns the PNG inline. The file is
    written to OS temp and the host copies it into AppData ``tool_captures``.
    ``width``/``height`` are recorded as the requested size only — the PNG
    matches the viewport.
    """
    raw = _capture_viewport_png()
    name = _capture_filename(filename)
    path = os.path.join(_captures_dir(), name)
    with open(path, "wb") as handle:
        handle.write(raw)
    out: dict[str, Any] = {
        "width": int(width),
        "height": int(height),
        "filename": name,
        "path": path,
        "bytes": len(raw),
        "method": _CAPTURE_TOOL,
        "viewport_capture": True,
    }
    return out


def play_in_editor() -> dict:
    """Start Play-In-Editor (best effort; may be restricted in some UEFN builds)."""
    sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    for method in ("editor_play_simulate", "editor_request_begin_play"):
        fn = getattr(sub, method, None)
        if callable(fn):
            fn()
            return {"started": True, "method": method}
    raise RuntimeError("No PIE start method available in this UEFN build")


def stop_pie() -> dict:
    """Stop Play-In-Editor / simulation (best effort)."""
    sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    fn = getattr(sub, "editor_end_play", None)
    if callable(fn):
        fn()
        return {"stopped": True}
    raise RuntimeError("No PIE stop method available in this UEFN build")


def set_object_property(asset_path: str, property_name: str, value: Any, save: bool = True) -> dict:
    """Set an editor property on a loaded asset object, then optionally save it."""
    obj = unreal.EditorAssetLibrary.load_asset(asset_path)
    if obj is None:
        raise ValueError(f"Asset not found: {asset_path}")
    obj.set_editor_property(property_name, value)
    if save:
        unreal.EditorAssetLibrary.save_loaded_asset(obj, only_if_is_dirty=False)
    return {
        "asset_path": asset_path,
        "property": property_name,
        "value": serialize(value),
        "saved": bool(save),
    }


def get_editor_stats() -> dict:
    """Lightweight editor/world summary for grounding agents."""
    world = _editor_world()
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    stats: dict[str, Any] = {
        "world_name": world.get_name() if world else None,
        "actor_count": len(actor_sub.get_all_level_actors()),
        "project_dir": str(unreal.Paths.project_dir()),
    }
    try:
        stats["engine_version"] = unreal.SystemLibrary.get_engine_version()
    except Exception:
        pass
    return stats


register("exec_console_command")(exec_console_command)
register("save_all_dirty")(save_all_dirty)
register("take_high_res_screenshot")(take_high_res_screenshot)
register("play_in_editor")(play_in_editor)
register("stop_pie")(stop_pie)
register("set_object_property")(set_object_property)
register("get_editor_stats")(get_editor_stats)
