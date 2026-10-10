"""Persist window position/size across sessions — main window + focus windows."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.Lock()


def _path() -> Path:
    # Resolved per call (was a module constant that ignored a later LOCALAPPDATA change).
    return Path(os.environ.get("LOCALAPPDATA", "")) / "UEFN-Ducky" / "window_bounds.json"


def _use_db() -> bool:
    from backend.store.switch import use_db

    return use_db("workspace_state")


def _doc(key: str):
    from backend.store.importers import phase1
    from backend.store.repos import kv

    phase1.ensure("workspace_state")
    return kv.get_doc("workspace_state", f"bounds:{key}")


def _load() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_bounds(key: str) -> dict[str, int] | None:
    b = None
    if _use_db():
        try:
            b = _doc(key)
        except (OSError, RuntimeError):
            b = None
    if b is None:
        b = _load().get(key)
    if isinstance(b, dict) and all(isinstance(b.get(k), (int, float)) for k in ("x", "y", "width", "height")):
        return {k: int(b[k]) for k in ("x", "y", "width", "height")}
    return None


def save_bounds(key: str, x: int, y: int, width: int, height: int) -> None:
    if width < 200 or height < 150:
        return
    value = {"x": int(x), "y": int(y), "width": int(width), "height": int(height)}
    if _use_db():
        try:
            from backend.store.importers import phase1
            from backend.store.repos import kv

            phase1.ensure("workspace_state")
            kv.set_doc("workspace_state", f"bounds:{key}", value)
            return
        except (OSError, RuntimeError):
            pass
    with _lock:
        data = _load()
        data[key] = value
        try:
            path = _path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass


def track(window: Any, key: str) -> None:
    """Save bounds on every move/resize (debounced) and on close — reopening the app
    or the same focus window restores it exactly where it was."""
    settle = 0.8
    # Moves and resizes arrive once per mouse move. One timer per drag pushes its
    # own deadline back; a new Timer thread per event was ~100 thread starts a second.
    lock = threading.Lock()
    state = {"due": 0.0, "armed": False}

    def snap() -> None:
        try:
            save_bounds(key, window.x, window.y, window.width, window.height)
        except Exception:
            pass

    def arm(delay: float) -> None:
        timer = threading.Timer(delay, fire)
        timer.daemon = True
        timer.start()

    def fire() -> None:
        with lock:
            wait = state["due"] - time.monotonic()
            if wait > 0.05:
                arm(wait)
                return
            state["armed"] = False
        snap()

    def schedule(*_args: object) -> None:
        with lock:
            state["due"] = time.monotonic() + settle
            if state["armed"]:
                return
            state["armed"] = True
        arm(settle)

    try:
        window.events.moved += schedule
    except Exception:
        pass
    try:
        window.events.resized += schedule
    except Exception:
        pass
    try:
        window.events.closing += snap
    except Exception:
        pass
