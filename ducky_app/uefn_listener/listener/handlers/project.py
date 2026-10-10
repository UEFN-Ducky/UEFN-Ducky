"""Project, level, viewport, editor log file."""

from typing import List, Optional

import unreal

from listener.dispatch import register
from listener.save_coalesce import save_now
from listener.serialize import rotator_pyr, serialize

# Other tools log into the same folder: revision control (Lore*.log, hundreds of MB
# after an hour), CEF (cef3.log), the VS Code plugin (urc*.log), and rotated copies
# carry a -backup- stamp. None of them hold Output Log lines, and the newest file
# was one of them about half the time.
_FOREIGN_LOG_PREFIXES = ("lore", "cef", "urc")
# Most bytes one call reads: a tail from the end of the log, a stream past its
# cursor. This runs on the game thread, so a 300 MB log must never be read whole.
_TAIL_BYTES = 1 << 20
_STREAM_BYTES = 4 << 20


def _editor_log_file(log_dir: str) -> Optional[str]:
    """The editor's own Output Log in ``log_dir``, or None."""
    import os

    names = [
        f
        for f in os.listdir(log_dir)
        if f.lower().endswith(".log")
        and "-backup-" not in f.lower()
        and not f.lower().startswith(_FOREIGN_LOG_PREFIXES)
    ]
    if not names:
        return None
    # UEFN names its log after the editor; a plain Unreal project after itself.
    for name in names:
        if name.lower() == "unrealeditorfortnite.log":
            return os.path.join(log_dir, name)
    names.sort(key=lambda f: os.path.getmtime(os.path.join(log_dir, f)), reverse=True)
    return os.path.join(log_dir, names[0])


@register("get_editor_log")
def cmd_get_editor_log(
    last_n: int = 100,
    filter_str: str = "",
    since_offset: int = 0,
    regex: str = "",
) -> dict:
    """Tail the editor's own .log.

    ``since_offset`` is a byte cursor — return only new bytes after that offset
    (for streaming during a play session), at most 4 MB per call; call again from
    the returned offset for the rest. ``regex`` filters lines when set;
    otherwise ``filter_str`` does a case-insensitive substring match.
    """
    import os
    import re

    log_file = None
    try:
        log_file = _editor_log_file(str(unreal.Paths.project_log_dir()))
    except Exception:
        pass

    if not log_file:
        return {"lines": [], "error": "Log file not found", "offset": 0}

    try:
        size = os.path.getsize(log_file)
        offset = max(0, int(since_offset or 0))
        if offset > size:
            offset = 0  # log rotated
        with open(log_file, "rb") as f:
            if offset > 0:
                f.seek(offset)
                data = f.read(_STREAM_BYTES)
                if len(data) == _STREAM_BYTES:
                    # Capped: end on a whole line so the next call resumes cleanly.
                    cut = data.rfind(b"\n")
                    if cut >= 0:
                        data = data[: cut + 1]
                new_offset = offset + len(data)
                lines = data.decode("utf-8", errors="replace").splitlines()
            else:
                start = max(0, size - _TAIL_BYTES)
                f.seek(start)
                data = f.read(_TAIL_BYTES)
                new_offset = start + len(data)
                if start > 0:
                    data = data[data.find(b"\n") + 1 :]  # the cut left half a line
                lines = data.decode("utf-8", errors="replace").splitlines()
                lines = lines[-max(1, int(last_n or 100)) :]
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
    world = unreal.EditorLevelLibrary.get_editor_world()
    project_name = ""
    content_root = ""
    if world:
        parts = world.get_path_name().split("/")
        if len(parts) >= 2:
            project_name = parts[1]
            content_root = f"/{project_name}/"
    return {
        "project_name": project_name,
        "content_root": content_root,
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
