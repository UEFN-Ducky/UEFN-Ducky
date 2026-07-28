"""Persist window position/size across sessions — main window + focus windows."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

_PATH = Path(os.environ.get("LOCALAPPDATA", "")) / "UEFN-Ducky" / "window_bounds.json"
_lock = threading.Lock()


def _load() -> dict[str, Any]:
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_bounds(key: str) -> dict[str, int] | None:
    b = _load().get(key)
    if isinstance(b, dict) and all(isinstance(b.get(k), (int, float)) for k in ("x", "y", "width", "height")):
        return {k: int(b[k]) for k in ("x", "y", "width", "height")}
    return None


def save_bounds(key: str, x: int, y: int, width: int, height: int) -> None:
    if width < 200 or height < 150:
        return
    with _lock:
        data = _load()
        data[key] = {"x": int(x), "y": int(y), "width": int(width), "height": int(height)}
        try:
            _PATH.parent.mkdir(parents=True, exist_ok=True)
            _PATH.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass


def track(window: Any, key: str) -> None:
    """Save bounds on every move/resize (debounced) and on close — reopening the app
    or the same focus window restores it exactly where it was."""
    pending: list[threading.Timer | None] = [None]

    def snap() -> None:
        try:
            save_bounds(key, window.x, window.y, window.width, window.height)
        except Exception:
            pass

    def schedule(*_args: object) -> None:
        t = pending[0]
        if t is not None:
            t.cancel()
        nt = threading.Timer(0.8, snap)
        nt.daemon = True
        pending[0] = nt
        nt.start()

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
