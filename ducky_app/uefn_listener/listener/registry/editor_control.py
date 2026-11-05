"""Editor control registry tools: console, PIE, screenshots, saving, properties."""

from __future__ import annotations

import base64
import json
import os
import struct
import tempfile
import time
from pathlib import Path
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


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_size(raw: bytes) -> tuple[int, int] | None:
    """(width, height) from the PNG's IHDR chunk, or None when it is not a PNG."""
    if len(raw) < 24 or not raw.startswith(_PNG_SIGNATURE) or raw[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", raw[16:24])
    return int(width), int(height)


def _capture_dir(save_dir: str) -> Path:
    """Where the PNG goes: the chat's attachments folder when the host passes one,
    else ``%TEMP%/ducky_captures`` (the host copies it into the chat and deletes it).
    Never the UEFN project."""
    if str(save_dir or "").strip():
        folder = Path(save_dir)
    else:
        folder = Path(tempfile.gettempdir()) / "ducky_captures"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _write_capture(raw: bytes, save_dir: str) -> Path:
    folder = _capture_dir(save_dir)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = folder / f"uefn_viewport_{stamp}_{os.getpid()}_{time.time_ns() % 1_000_000:06d}.png"
    part = path.with_name(path.name + ".part")
    part.write_bytes(raw)
    os.replace(part, path)  # the host never reads a half-written PNG
    return path


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


def take_high_res_screenshot(
    width: int = 1280, height: int = 720, filename: str = "", save_dir: str = ""
) -> dict:
    """Capture the active viewport without freezing the editor.

    **Never** uses ``AutomationLibrary`` / ``HighResShot`` — those do a
    synchronous offscreen render on the Slate tick and can freeze UEFN for tens
    of seconds on dense levels. **Never** uses the ``Shot`` console command
    either: that one is a *game* viewport command and writes nothing at all in
    the editor, which is how captures used to "start" and never produce a PNG.

    Goes through Epic's ``EditorAppToolset.CaptureViewport``, which reads the
    viewport back on the calling frame. The PNG is written to a file (``save_dir``,
    the chat's attachments folder, when the host passes it) and the reply carries
    only ``path``, ``width`` and ``height``; the host builds the image block from
    the file. Inline base64 is never sent: a 1280x720 PNG is ~2.2 MB as base64,
    over the 2 MB reply cap (``listener.limits``), which cut the reply down to
    ``truncated: true`` with no image. ``width``/``height`` are the PNG's real
    size; the asked-for size is ``requested_width``/``requested_height``.
    ``filename`` is unused (kept so older callers still match).
    """
    del filename  # host names the chat-folder file
    raw = _capture_viewport_png()
    path = _write_capture(raw, save_dir)
    size = _png_size(raw) or (int(width), int(height))
    # The capture's render targets stay pinned until the viewport draws again
    # (editor viewports are not realtime): ~700 MB per capture, see render_trim.
    from listener import render_trim

    render_trim.request_trim()
    return {
        "ok": True,
        "path": str(path),
        "width": size[0],
        "height": size[1],
        "requested_width": int(width),
        "requested_height": int(height),
        "bytes": len(raw),
        "method": _CAPTURE_TOOL,
        "viewport_capture": True,
    }


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


def render_trim(ticks: int = 0) -> dict:
    """Redraw the level viewports for a burst so the render-target pool can free
    what an off-screen render left behind (the host calls this after a capture
    that went through Epic's MCP instead of this listener)."""
    from listener import render_trim as _trim

    n = int(ticks) if int(ticks or 0) > 0 else _trim.TRIM_TICKS
    _trim.request_trim(n)
    return {"ticks": n, "pending": _trim.pending()}


register("exec_console_command")(exec_console_command)
register("save_all_dirty")(save_all_dirty)
register("take_high_res_screenshot")(take_high_res_screenshot)
register("render_trim")(render_trim)
def move_player_pawn(
    label: str = "",
    x: float | None = None,
    y: float | None = None,
    z: float | None = None,
) -> dict:
    """Move the first game-world player pawn to a labeled actor or x,y,z."""
    gw = None
    try:
        gw = unreal.EditorLevelLibrary.get_game_world()
    except Exception:
        gw = None
    if gw is None:
        return {"ok": False, "error": "no game world — start play first"}
    pawn = None
    try:
        pc = unreal.GameplayStatics.get_player_controller(gw, 0)
        pawn = pc.get_pawn() if pc is not None and hasattr(pc, "get_pawn") else None
    except Exception:
        pawn = None
    if pawn is None:
        return {"ok": False, "error": "no player pawn"}
    loc = None
    name = (label or "").strip()
    if name:
        sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        for actor in list(sub.get_all_level_actors() or []):
            try:
                if str(actor.get_actor_label()) == name:
                    loc = actor.get_actor_location()
                    break
            except Exception:
                continue
        if loc is None:
            return {"ok": False, "error": f"actor not found: {name}"}
    elif x is not None and y is not None and z is not None:
        loc = unreal.Vector(float(x), float(y), float(z))
    else:
        return {"ok": False, "error": "label or x,y,z required"}
    pawn.set_actor_location(loc, False, False)
    return {"ok": True, "label": name, "location": {"x": loc.x, "y": loc.y, "z": loc.z}}


register("play_in_editor")(play_in_editor)
register("stop_pie")(stop_pie)
register("move_player_pawn")(move_player_pawn)
register("set_object_property")(set_object_property)
register("get_editor_stats")(get_editor_stats)
