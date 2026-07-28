"""Editor control registry tools: console, PIE, screenshots, saving, properties."""

from __future__ import annotations

import os
import time
from typing import Any

import unreal

from listener.dispatch import register
from listener.serialize import serialize


def _editor_world():
    return unreal.EditorLevelLibrary.get_editor_world()


def _resolve_screenshot_path(filename: str) -> str:
    """Locate the PNG UE just wrote under Saved/Screenshots (or absolute path)."""
    name = os.path.basename(filename or "")
    if not name:
        return ""
    if os.path.isabs(filename) and os.path.isfile(filename):
        return filename
    try:
        project_dir = unreal.Paths.project_saved_dir()
    except Exception:
        project_dir = ""
    roots = []
    if project_dir:
        roots.append(os.path.join(project_dir, "Screenshots"))
        roots.append(project_dir)
    # Newest match by mtime within a short window after capture.
    newest = ""
    newest_mtime = 0.0
    cutoff = time.time() - 120.0
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            if name not in filenames:
                continue
            path = os.path.join(dirpath, name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime >= cutoff and mtime >= newest_mtime:
                newest = path
                newest_mtime = mtime
    if newest:
        return newest
    # Fallback: any matching name under Screenshots
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            if name in filenames:
                return os.path.join(dirpath, name)
    return ""


def exec_console_command(command: str) -> dict:
    """Run an editor console command (e.g. 'stat fps', 'r.ScreenPercentage 100')."""
    if not command.strip():
        raise ValueError("command must not be empty")
    world = _editor_world()
    unreal.SystemLibrary.execute_console_command(world, command)
    return {"command": command, "executed": True}


def save_all_dirty(content: bool = True, maps: bool = True) -> dict:
    """Save all dirty content packages and/or maps without prompting."""
    ok = unreal.EditorLoadingAndSavingUtils.save_dirty_packages(save_map_packages=bool(maps), save_content_packages=bool(content))
    return {"saved": bool(ok), "content": content, "maps": maps}


def take_high_res_screenshot(width: int = 1920, height: int = 1080, filename: str = "") -> dict:
    """Capture a high-resolution screenshot of the active viewport.

    Returns absolute ``path`` when the file can be resolved under Saved/Screenshots.
    """
    fn = filename or "uefn_ducky_screenshot.png"
    try:
        unreal.AutomationLibrary.take_high_res_screenshot(int(width), int(height), fn)
    except Exception as e:
        raise RuntimeError(f"Screenshot failed: {e}") from e
    # UE may finish the write slightly after the call returns.
    path = ""
    for _ in range(8):
        path = _resolve_screenshot_path(fn)
        if path:
            break
        time.sleep(0.05)
    out = {"width": int(width), "height": int(height), "filename": os.path.basename(fn)}
    if path:
        out["path"] = path
    else:
        out["hint"] = (
            "Screenshot taken but path not resolved — do not Bash-find; "
            "re-call take_high_res_screenshot or check Project/Saved/Screenshots."
        )
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
    return {"asset_path": asset_path, "property": property_name, "value": serialize(value), "saved": bool(save)}


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
