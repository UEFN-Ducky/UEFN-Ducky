"""Native splash shown before heavy panel imports (so double-click is never a blank wait).

Floating duck only — no black box, no "Starting…" text. Press-and-hold to drag.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Color-keyed away so only the duck (or fallback text) is visible.
_KEY = "#0a0a0a"
_KEY_RGB = (0x0A, 0x0A, 0x0A)
# Soft PNG edges composite onto the key and leave a dark halo that is *not*
# exact #0a0a0a, so -transparentcolor cannot punch them out. Binary-matte first.
_ALPHA_CUTOFF = 40
_DUCK_PX = 220
_PAD = 10

_splash: Any | None = None
_photo: Any | None = None  # keep PhotoImage alive
_drag: dict[str, int] = {"x": 0, "y": 0}


def _matte_for_colorkey(img: Any) -> Any:
    """Hard-matte RGBA for Win32 ``-transparentcolor`` (no fringe / fake shadow)."""
    rgba = img.convert("RGBA")
    kr, kg, kb = _KEY_RGB
    out: list[tuple[int, int, int, int]] = []
    for r, g, b, a in rgba.getdata():
        if int(a) < _ALPHA_CUTOFF:
            out.append((kr, kg, kb, 255))
            continue
        if (int(r), int(g), int(b)) == (kr, kg, kb):
            r = min(255, int(r) + 1)
        out.append((int(r), int(g), int(b), 255))
    rgba.putdata(out)
    return rgba


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
    """Paint a topmost floating duck immediately. Call from the main thread only."""
    global _splash, _photo
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
    root.configure(bg=_KEY)
    width = height = _DUCK_PX + _PAD * 2
    try:
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        x = max(0, (sw - width) // 2)
        y = max(0, (sh - height) // 2)
    except Exception:
        x, y = 200, 160
    root.geometry(f"{width}x{height}+{x}+{y}")
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    try:
        root.attributes("-transparentcolor", _KEY)
    except Exception:
        pass

    frame = tk.Frame(root, bg=_KEY, borderwidth=0, highlightthickness=0)
    frame.place(relx=0.5, rely=0.5, anchor="center")

    logo = _logo_png()
    label: Any | None = None
    if logo is not None:
        try:
            from PIL import Image, ImageTk

            img = Image.open(logo).convert("RGBA").resize((_DUCK_PX, _DUCK_PX), Image.Resampling.LANCZOS)
            img = _matte_for_colorkey(img)
            _photo = ImageTk.PhotoImage(img)
            label = tk.Label(frame, image=_photo, bg=_KEY, borderwidth=0, highlightthickness=0)
            label.pack()
        except Exception:
            label = None
    if label is None:
        label = tk.Label(
            frame,
            text="UEFN Ducky",
            fg="#ededed",
            bg=_KEY,
            font=("Segoe UI", 18, "bold"),
            borderwidth=0,
            highlightthickness=0,
        )
        label.pack()

    _bind_drag(label, root)
    _bind_drag(frame, root)
    _bind_drag(root, root)

    try:
        root.update_idletasks()
        root.update()
    except Exception:
        pass

    _splash = root
    return root


def take_as_pump_root() -> Any | None:
    """Convert the splash Tk into the hidden tray/pump root (one Tcl interpreter)."""
    global _splash, _photo
    root = _splash
    _splash = None
    _photo = None
    if root is None:
        return None
    try:
        for child in list(root.winfo_children()):
            child.destroy()
    except Exception:
        pass
    try:
        root.unbind("<Button-1>")
        root.unbind("<B1-Motion>")
    except Exception:
        pass
    try:
        root.attributes("-transparentcolor", "")
    except Exception:
        pass
    try:
        root.overrideredirect(False)
    except Exception:
        pass
    try:
        root.attributes("-topmost", False)
    except Exception:
        pass
    try:
        root.withdraw()
    except Exception:
        pass
    return root


def dismiss() -> None:
    """Close the early splash (idempotent). Call from the same thread as show()."""
    global _splash, _photo
    root = _splash
    _splash = None
    _photo = None
    if root is None:
        return
    try:
        root.destroy()
    except Exception:
        pass


if __name__ == "__main__":
    # Demo: floating duck for 5s — drag it around, then exit.
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
