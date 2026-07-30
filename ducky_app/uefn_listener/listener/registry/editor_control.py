"""Editor control registry tools: console, PIE, screenshots, saving, properties."""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import unreal

from listener.dispatch import register
from listener.serialize import serialize


def _editor_world():
    return unreal.EditorLevelLibrary.get_editor_world()


def _screenshots_dir() -> str:
    try:
        project_dir = unreal.Paths.project_saved_dir()
    except Exception:
        project_dir = ""
    if not project_dir:
        return ""
    return os.path.join(project_dir, "Screenshots")


def _expected_screenshot_path(filename: str) -> str:
    name = os.path.basename(filename or "")
    if not name:
        return ""
    root = _screenshots_dir()
    return os.path.join(root, name) if root else name


def _resolve_screenshot_path(filename: str, *, since: float = 0.0) -> str:
    """Locate a fresh PNG under Saved/Screenshots only (never walk all of Saved)."""
    name = os.path.basename(filename or "")
    if not name:
        return ""
    if os.path.isabs(filename) and os.path.isfile(filename):
        try:
            if since <= 0.0 or os.path.getmtime(filename) >= since - 1.0:
                return filename
        except OSError:
            return filename
    root = _screenshots_dir()
    if not root or not os.path.isdir(root):
        return ""
    # Non-recursive first (UE writes here directly).
    direct = os.path.join(root, name)
    if os.path.isfile(direct):
        try:
            if since <= 0.0 or os.path.getmtime(direct) >= since - 1.0:
                return direct
        except OSError:
            return direct
    # One level of subdirs only (Windows/UE sometimes nests by map name).
    try:
        for entry in os.listdir(root):
            sub = os.path.join(root, entry)
            if not os.path.isdir(sub):
                continue
            path = os.path.join(sub, name)
            if not os.path.isfile(path):
                continue
            try:
                if since <= 0.0 or os.path.getmtime(path) >= since - 1.0:
                    return path
            except OSError:
                return path
    except OSError:
        pass
    return ""


def _newest_screenshot_since(since: float) -> str:
    """Newest .png under Screenshots modified at/after ``since`` (shallow)."""
    root = _screenshots_dir()
    if not root or not os.path.isdir(root):
        return ""
    newest = ""
    newest_mtime = 0.0
    cutoff = since - 1.0
    candidates: list[str] = []
    try:
        for entry in os.listdir(root):
            path = os.path.join(root, entry)
            if os.path.isfile(path) and entry.lower().endswith(".png"):
                candidates.append(path)
            elif os.path.isdir(path):
                try:
                    for child in os.listdir(path):
                        if child.lower().endswith(".png"):
                            candidates.append(os.path.join(path, child))
                except OSError:
                    continue
    except OSError:
        return ""
    for path in candidates:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime >= cutoff and mtime >= newest_mtime:
            newest = path
            newest_mtime = mtime
    return newest


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

    **Never** uses ``AutomationLibrary.take_high_res_screenshot`` / HighResShot —
    those do a synchronous offscreen render on the Slate tick and can freeze UEFN
    for tens of seconds on dense levels (then the host hits its 30s bridge timeout).

    Captures the current viewport buffer via ``take_screenshot`` (or console
    ``Shot``). ``width``/``height`` are recorded as requested size only — the PNG
    matches the viewport. Does not sleep on the game thread; the host waits for
    the file if the write lands on the next frame.
    """
    req_w, req_h = int(width), int(height)
    # Unique name so resolve never picks a stale prior capture.
    raw = os.path.basename((filename or "").strip())
    if raw.lower().endswith(".png") and raw.lower() != ".png":
        stem = raw[:-4]
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in stem)[:48] or "uefn_ducky"
    else:
        safe = "uefn_ducky"
    fn = f"{safe}_{int(time.time())}_{uuid.uuid4().hex[:8]}.png"
    started = time.time()
    method = ""
    try:
        take_fn = getattr(unreal.AutomationLibrary, "take_screenshot", None)
        if callable(take_fn):
            take_fn(fn)
            method = "take_screenshot"
        else:
            world = _editor_world()
            unreal.SystemLibrary.execute_console_command(world, "Shot")
            method = "Shot"
            fn = ""
    except Exception as e:
        raise RuntimeError(f"Screenshot failed: {e}") from e

    # Do NOT time.sleep here — sleeping on the Slate tick freezes the entire editor
    # and can prevent the async PNG write from finishing.
    path = ""
    if fn:
        path = _resolve_screenshot_path(fn, since=started)
    if not path:
        path = _newest_screenshot_since(started)
    expected = path or (_expected_screenshot_path(fn) if fn else "")
    out: dict[str, Any] = {
        "width": req_w,
        "height": req_h,
        "filename": os.path.basename(expected or fn or "screenshot.png"),
        "method": method,
        "viewport_capture": True,
    }
    if expected:
        out["path"] = expected
    if not path:
        out["await_path"] = True
        out["hint"] = (
            "Viewport capture kicked off — PNG may appear on the next editor frame. "
            "Host waits briefly for path; do not Bash-find."
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
