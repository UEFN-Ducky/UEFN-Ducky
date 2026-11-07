"""Native first frame shown before heavy panel imports (so double-click is never a blank wait).

The app's own outline, not a logo: a dark frame with its top bar, exactly where the main
window opens (its saved bounds), so the window seems to open at once and the real one
replaces it in place. Press-and-hold to drag.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# The theme background and border (critical.css --bg / --border), the header's height.
_BG = "#0a0a0a"
_BORDER = "#262626"
_HEADER_PX = 34
# webview_app.py's first-run size.
_DEFAULT_W, _DEFAULT_H = 920, 640

_splash: Any | None = None
_drag: dict[str, int] = {"x": 0, "y": 0}


def _logo_png() -> Path | None:
    candidates: list[Path] = []
    try:
        from frontend.bundle_root import is_packaged_runtime, packaged_data_root

        if is_packaged_runtime():
            root = packaged_data_root()
            if root:
                candidates.append(root / "frontend" / "ui_web" / "web" / "dist" / "OnlineMCPIcon.png")
    except Exception:
        pass
    here = Path(__file__).resolve().parent / "ui_web" / "web"
    candidates.extend(
        (
            here / "dist" / "OnlineMCPIcon.png",
            here / "public" / "OnlineMCPIcon.png",
        )
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def frame_rect(saved: dict[str, int] | None, screen_w: int, screen_h: int) -> tuple[int, int, int, int]:
    """Where the main window will open: its saved bounds, or the first-run size centred."""
    if saved and saved.get("width", 0) >= 200 and saved.get("height", 0) >= 150:
        return saved["x"], saved["y"], saved["width"], saved["height"]
    w, h = _DEFAULT_W, _DEFAULT_H
    return max(0, (screen_w - w) // 2), max(0, (screen_h - h) // 2), w, h


def _saved_bounds() -> dict[str, int] | None:
    try:
        from frontend.ui_web.window_bounds import get_bounds

        return get_bounds("main")
    except Exception:
        return None


def _bind_drag(widget: Any, root: Any) -> None:
    def on_press(event: Any) -> None:
        _drag["x"] = int(event.x_root) - int(root.winfo_x())
        _drag["y"] = int(event.y_root) - int(root.winfo_y())

    def on_motion(event: Any) -> None:
        x = int(event.x_root) - _drag["x"]
        y = int(event.y_root) - _drag["y"]
        root.geometry(f"+{x}+{y}")

    widget.bind("<Button-1>", on_press)
    widget.bind("<B1-Motion>", on_motion)


def show() -> Any | None:
    """Paint the app's outline where its window opens. Call from the main thread only."""
    global _splash
    if sys.platform != "win32":
        return None
    if _splash is not None:
        return _splash
    try:
        import tkinter as tk
    except Exception:
        return None

    root = tk.Tk()
    root.overrideredirect(True)
    root.configure(bg=_BG)
    try:
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    except Exception:
        sw, sh = _DEFAULT_W + 400, _DEFAULT_H + 320
    x, y, w, h = frame_rect(_saved_bounds(), sw, sh)
    root.geometry(f"{w}x{h}+{x}+{y}")

    bar = tk.Frame(root, bg=_BG, height=_HEADER_PX, borderwidth=0, highlightthickness=0)
    bar.pack(side="top", fill="x")
    line = tk.Frame(root, bg=_BORDER, height=1, borderwidth=0, highlightthickness=0)
    line.pack(side="top", fill="x")
    _bind_drag(bar, root)
    _bind_drag(root, root)

    try:
        root.update_idletasks()
        root.update()
    except Exception:
        pass

    _splash = root
    return root


def dismiss() -> None:
    """Close the early splash (idempotent). Call from the same thread as show()."""
    global _splash
    root = _splash
    _splash = None
    if root is None:
        return
    try:
        root.destroy()
    except Exception:
        pass


if __name__ == "__main__":
    # Demo: the outline for 5s — drag it around, then exit.
    import time

    show()
    try:
        t_end = time.time() + 5.0
        while time.time() < t_end and _splash is not None:
            try:
                _splash.update()
            except Exception:
                break
            time.sleep(0.03)
    finally:
        dismiss()
