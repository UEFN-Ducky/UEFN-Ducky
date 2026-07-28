"""Editor control registry tools: console, PIE, screenshots, saving, properties."""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import unreal

from listener.dispatch import register
from listener.serialize import serialize
from listener.screenshot_util import (
    CAPTURE_TIMEOUT_SEC,
    CaptureStore,
    resolve_screenshot_path,
    unique_screenshot_filename,
)
from listener.state import response_events, responses, responses_lock

# Survives listener reloads when attached to unreal; replace stale class instances.
_store = getattr(unreal, "_mcp_screenshot_captures", None)
if not isinstance(_store, CaptureStore) or not hasattr(_store, "items"):
    unreal._mcp_screenshot_captures = CaptureStore()
_CAPTURES: CaptureStore = unreal._mcp_screenshot_captures


def _editor_world():
    return unreal.EditorLevelLibrary.get_editor_world()


def _screenshot_roots() -> list[str]:
    try:
        project_dir = unreal.Paths.project_saved_dir()
    except Exception:
        project_dir = ""
    if not project_dir:
        return []
    return [os.path.join(project_dir, "Screenshots")]


def _resolve_path(filename: str, *, started_at: float = 0.0) -> str:
    return resolve_screenshot_path(filename, _screenshot_roots(), started_at=started_at)


def _evaluate_capture(capture_id: str, rec: dict[str, Any]) -> dict[str, Any]:
    """Return status payload for a pending capture (does not mutate store)."""
    filename = str(rec.get("filename") or "")
    started_at = float(rec.get("started_at") or 0.0)
    width = int(rec.get("width") or 0)
    height = int(rec.get("height") or 0)
    task = rec.get("task")
    now = time.time()
    age = now - started_at if started_at else 0.0

    path = _resolve_path(filename, started_at=started_at)
    if path:
        return {
            "status": "completed",
            "capture_id": capture_id,
            "width": width,
            "height": height,
            "filename": os.path.basename(filename),
            "path": path,
        }

    task_done = False
    task_valid = True
    if task is not None:
        try:
            is_valid = getattr(task, "is_valid_task", None)
            if callable(is_valid):
                task_valid = bool(is_valid())
            is_done = getattr(task, "is_task_done", None)
            if callable(is_done):
                task_done = bool(is_done())
        except Exception:
            task_valid = False

    if not task_valid:
        return {
            "status": "failed",
            "capture_id": capture_id,
            "width": width,
            "height": height,
            "filename": os.path.basename(filename),
            "error": "Screenshot task became invalid before the file was written",
        }

    if age >= CAPTURE_TIMEOUT_SEC:
        return {
            "status": "timed_out",
            "capture_id": capture_id,
            "width": width,
            "height": height,
            "filename": os.path.basename(filename),
            "error": (
                f"Screenshot timed out after {int(CAPTURE_TIMEOUT_SEC)}s "
                "(file not found under Saved/Screenshots)"
            ),
        }

    return {
        "status": "pending",
        "capture_id": capture_id,
        "width": width,
        "height": height,
        "filename": os.path.basename(filename),
        "task_done": task_done,
        "age_sec": round(age, 2),
    }


def pump_deferred_screenshots() -> None:
    """Complete HTTP requests waiting on cross-tick screenshot captures.

    Called from the Slate tick so frames can advance while ``AutomationEditorTask``
    finishes. Safe for the packaged MCP host that only issues one command.
    """
    for capture_id, rec in _CAPTURES.items():
        req_id = str(rec.get("req_id") or "").strip()
        if not req_id:
            continue
        out = _evaluate_capture(capture_id, rec)
        status = str(out.get("status") or "")
        if status == "pending":
            continue
        _CAPTURES.discard(capture_id)
        response = {"success": status == "completed", "result": out}
        if status != "completed":
            response = {
                "success": False,
                "error": str(out.get("error") or f"Screenshot {status}"),
                "result": out,
            }
        ev = None
        with responses_lock:
            responses[req_id] = response
            ev = response_events.pop(req_id, None)
        if ev is not None:
            ev.set()


def bind_deferred_screenshot(capture_id: str, req_id: str) -> bool:
    """Attach an HTTP request id to a pending capture so pump can complete it."""
    rec = _CAPTURES.get(capture_id)
    if rec is None:
        return False
    rec["req_id"] = req_id
    return True


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
    width: int = 1920,
    height: int = 1080,
    filename: str = "",
    wait: bool = True,
) -> dict:
    """Start an async high-res viewport screenshot (does not block the editor tick).

    ``wait=True`` (default): return a defer marker so the HTTP request stays open
    while later ticks finish the ``AutomationEditorTask`` — works with older MCP
    hosts that only call this once.

    ``wait=False``: return ``status=pending`` + ``capture_id`` immediately; host
    should call ``poll_screenshot_capture`` on later ticks.
    """
    _CAPTURES.prune_expired()
    fn = unique_screenshot_filename(filename)
    capture_id = uuid.uuid4().hex
    started_at = time.time()
    w, h = int(width), int(height)

    try:
        # force_game_view=False: capture the editor viewport. True can hang forever
        # when game view / streaming is unavailable (common in UEFN edit sessions).
        # Use positional args — some UEFN builds ignore the force_game_view kwarg.
        task = unreal.AutomationLibrary.take_high_res_screenshot(
            w,
            h,
            fn,
            None,
            False,
            False,
            unreal.ComparisonTolerance.LOW,
            "",
            0.1,
            False,
        )
    except TypeError:
        try:
            task = unreal.AutomationLibrary.take_high_res_screenshot(
                w, h, fn, force_game_view=False
            )
        except TypeError:
            try:
                task = unreal.AutomationLibrary.take_high_res_screenshot(w, h, fn)
            except Exception as e:
                raise RuntimeError(f"Screenshot failed: {e}") from e
        except Exception as e:
            raise RuntimeError(f"Screenshot failed: {e}") from e
    except Exception as e:
        raise RuntimeError(f"Screenshot failed: {e}") from e

    path = _resolve_path(fn, started_at=started_at)
    if path:
        return {
            "status": "completed",
            "capture_id": capture_id,
            "width": w,
            "height": h,
            "filename": os.path.basename(fn),
            "path": path,
        }

    _CAPTURES.put(
        capture_id,
        {
            "task": task,
            "filename": fn,
            "started_at": started_at,
            "width": w,
            "height": h,
        },
    )
    if wait:
        # Tick will bind req_id and complete the HTTP response across frames.
        return {
            "_ducky_defer": True,
            "capture_id": capture_id,
            "status": "pending",
            "width": w,
            "height": h,
            "filename": os.path.basename(fn),
        }
    return {
        "status": "pending",
        "capture_id": capture_id,
        "width": w,
        "height": h,
        "filename": os.path.basename(fn),
        "hint": "Poll poll_screenshot_capture with this capture_id until completed.",
    }


def poll_screenshot_capture(capture_id: str = "") -> dict:
    """Poll a pending screenshot started by ``take_high_res_screenshot``.

    Lightweight — must NOT be treated as a heavy editor command so it can run
    on the same tick budget as other work. Returns status pending / completed /
    failed / timed_out.
    """
    cid = (capture_id or "").strip()
    if not cid:
        raise ValueError("capture_id is required")
    rec = _CAPTURES.get(cid)
    if rec is None:
        return {
            "status": "failed",
            "capture_id": cid,
            "error": "Unknown or expired capture_id",
        }
    out = _evaluate_capture(cid, rec)
    if out.get("status") != "pending":
        _CAPTURES.discard(cid)
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
register("poll_screenshot_capture")(poll_screenshot_capture)
register("play_in_editor")(play_in_editor)
register("stop_pie")(stop_pie)
register("set_object_property")(set_object_property)
register("get_editor_stats")(get_editor_stats)
