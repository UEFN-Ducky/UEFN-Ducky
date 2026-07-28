"""Project, level, viewport, editor log file."""

from typing import List, Optional

import unreal

from listener.dispatch import register
from listener.save_coalesce import save_now
from listener.serialize import rotator_pyr, serialize


@register("get_editor_log")
def cmd_get_editor_log(
    last_n: int = 100,
    filter_str: str = "",
    since_offset: int = 0,
    regex: str = "",
) -> dict:
    """Tail the newest editor .log.

    ``since_offset`` is a byte cursor — return only new bytes after that offset
    (for streaming during a play session). ``regex`` filters lines when set;
    otherwise ``filter_str`` does a case-insensitive substring match.
    """
    import os
    import re

    log_path = unreal.Paths.project_log_dir()
    log_file = None
    try:
        log_dir = str(log_path)
        log_files = [f for f in os.listdir(log_dir) if f.endswith(".log")]
        if log_files:
            log_files.sort(key=lambda f: os.path.getmtime(os.path.join(log_dir, f)), reverse=True)
            log_file = os.path.join(log_dir, log_files[0])
    except Exception:
        pass

    if not log_file:
        return {"lines": [], "error": "Log file not found", "offset": 0}

    try:
        size = os.path.getsize(log_file)
        offset = max(0, int(since_offset or 0))
        if offset > size:
            offset = 0  # log rotated
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            if offset > 0:
                f.seek(offset)
                chunk = f.read()
                new_offset = f.tell()
                lines = chunk.splitlines()
            else:
                all_lines = f.readlines()
                new_offset = f.tell()
                lines = all_lines[-max(1, int(last_n or 100)) :]
        if regex:
            try:
                pat = re.compile(regex)
                lines = [ln for ln in lines if pat.search(ln)]
            except re.error as exc:
                return {
                    "lines": [],
                    "error": f"invalid regex: {exc}",
                    "file": log_file,
                    "offset": new_offset,
                }
        elif filter_str:
            needle = filter_str.lower()
            lines = [ln for ln in lines if needle in ln.lower()]
        return {
            "lines": [ln.rstrip() for ln in lines],
            "count": len(lines),
            "file": log_file,
            "offset": new_offset,
            "size": size,
        }
    except Exception as e:
        return {"lines": [], "error": str(e), "offset": 0}


@register("get_project_info")
def cmd_get_project_info() -> dict:
    from listener.project_paths import content_root

    world = unreal.EditorLevelLibrary.get_editor_world()
    root = content_root()
    project_name = root.strip("/") if root else ""
    return {
        "project_name": project_name,
        "content_root": f"{root}/" if root and not root.endswith("/") else root,
        "project_dir": str(unreal.Paths.project_dir()),
    }


@register("save_current_level")
def cmd_save_current_level() -> dict:
    return {"success": save_now()}


@register("get_level_info")
def cmd_get_level_info() -> dict:
    world = unreal.EditorLevelLibrary.get_editor_world()
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors = actor_sub.get_all_level_actors()
    return {
        "world_name": world.get_name() if world else "None",
        "actor_count": len(actors),
    }


@register("get_viewport_camera")
def cmd_get_viewport_camera() -> dict:
    loc, rot = unreal.EditorLevelLibrary.get_level_viewport_camera_info()
    return {"location": serialize(loc), "rotation": serialize(rot)}


@register("set_viewport_camera")
def cmd_set_viewport_camera(
    location: Optional[List[float]] = None,
    rotation: Optional[List[float]] = None,
) -> dict:
    cur_loc, cur_rot = unreal.EditorLevelLibrary.get_level_viewport_camera_info()
    loc = unreal.Vector(*location) if location else cur_loc
    rot = rotator_pyr(*rotation) if rotation else cur_rot
    unreal.EditorLevelLibrary.set_level_viewport_camera_info(loc, rot)
    return {"location": serialize(loc), "rotation": serialize(rot)}
