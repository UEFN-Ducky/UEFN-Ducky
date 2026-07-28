"""Fullscreen click-through confetti on the desktop (Windows)."""

from __future__ import annotations

import ctypes
import math
import random
import sys
import tkinter as tk
from typing import Any

_COLORS = ("#ff424d", "#f96854", "#ffd700", "#ff6b9d", "#4ecdc4", "#ffffff", "#ff9f43", "#a78bfa")
# Magenta chroma key — none of the confetti palette uses this hue.
_TRANSPARENT_BG = "#ff00ff"


def _virtual_screen() -> tuple[int, int, int, int]:
    if sys.platform == "win32":
        user32 = ctypes.windll.user32
        return (
            int(user32.GetSystemMetrics(76)),
            int(user32.GetSystemMetrics(77)),
            int(user32.GetSystemMetrics(78)),
            int(user32.GetSystemMetrics(79)),
        )
    return 0, 0, 1920, 1080


def _make_click_through(hwnd: int) -> None:
    if sys.platform != "win32" or not hwnd:
        return
    user32 = ctypes.windll.user32
    gwl_exstyle = -20
    ws_ex_layered = 0x00080000
    ws_ex_transparent = 0x00000020
    get_ex = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
    set_ex = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
    get_ex.restype = ctypes.c_ssize_t
    set_ex.restype = ctypes.c_ssize_t
    style = int(get_ex(hwnd, gwl_exstyle))
    set_ex(hwnd, gwl_exstyle, style | ws_ex_layered | ws_ex_transparent)


def _resolve_hwnd(widget: tk.Misc) -> int:
    return int(widget.winfo_id())


def burst_desktop_confetti(tk_root: Any, screen_x: float, screen_y: float, *, count: int = 120) -> None:
    """Spawn a short-lived fullscreen overlay with confetti at desktop coordinates."""
    if tk_root is None or sys.platform != "win32":
        return

    left, top, vw, vh = _virtual_screen()
    origin_x = float(screen_x) - left
    origin_y = float(screen_y) - top

    win = tk.Toplevel(tk_root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.geometry(f"{vw}x{vh}+{left}+{top}")
    win.configure(bg=_TRANSPARENT_BG)
    win.attributes("-transparentcolor", _TRANSPARENT_BG)

    canvas = tk.Canvas(
        win,
        width=vw,
        height=vh,
        bg=_TRANSPARENT_BG,
        highlightthickness=0,
        bd=0,
    )
    canvas.pack(fill="both", expand=True)

    particles: list[dict[str, float | int | str]] = []
    for _ in range(count):
        angle = random.random() * math.tau
        speed = 5 + random.random() * 12
        particles.append(
            {
                "x": origin_x,
                "y": origin_y,
                "vx": math.cos(angle) * speed,
                "vy": math.sin(angle) * speed - 5,
                "w": 4 + random.random() * 7,
                "h": 5 + random.random() * 9,
                "color": random.choice(_COLORS),
                "life": 0,
                "max_life": 55 + random.random() * 45,
                "item": 0,
            }
        )

    win.update_idletasks()
    win.lift()
    win.update()

    def enable_click_through() -> None:
        try:
            if win.winfo_exists():
                _make_click_through(_resolve_hwnd(win))
        except Exception:
            pass

    # Let the first frame paint before WS_EX_TRANSPARENT (that flag can hide the layer).
    win.after(120, enable_click_through)

    def tick() -> None:
        if not win.winfo_exists():
            return
        alive: list[dict[str, float | int | str]] = []
        for p in particles:
            life = int(p["life"]) + 1
            p["life"] = life
            p["vy"] = float(p["vy"]) + 0.38
            p["vx"] = float(p["vx"]) * 0.985
            p["x"] = float(p["x"]) + float(p["vx"])
            p["y"] = float(p["y"]) + float(p["vy"])

            alpha = 1.0 - life / float(p["max_life"])
            if alpha <= 0:
                item_id = int(p["item"])
                if item_id:
                    canvas.delete(item_id)
                continue

            item_id = int(p["item"])
            x = float(p["x"])
            y = float(p["y"])
            hw = float(p["w"]) / 2
            hh = float(p["h"]) / 2
            if item_id:
                canvas.coords(item_id, x - hw, y - hh, x + hw, y + hh)
            else:
                p["item"] = canvas.create_rectangle(
                    x - hw,
                    y - hh,
                    x + hw,
                    y + hh,
                    fill=str(p["color"]),
                    outline="",
                )
            alive.append(p)

        particles[:] = alive
        if particles:
            win.after(16, tick)
        else:
            win.destroy()

    win.after(16, tick)


def schedule_desktop_confetti(tk_root: Any, screen_x: float, screen_y: float) -> None:
    """Thread-safe entry: marshal overlay creation onto the Tk pump thread."""
    if tk_root is None or sys.platform != "win32":
        return

    def _spawn() -> None:
        burst_desktop_confetti(tk_root, screen_x, screen_y)

    from frontend.ui_web.shutdown import schedule_tk_call

    schedule_tk_call(_spawn)
