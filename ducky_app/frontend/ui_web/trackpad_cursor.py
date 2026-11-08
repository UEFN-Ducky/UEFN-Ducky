"""Big Ducky pointer: grows in while the phone trackpad moves the mouse, shrinks away after.

A click-through layered window follows the real cursor, tips lined up, so nothing about the
PC's own cursor changes and a crash cannot leave it altered.

ponytail: Windows draws the real cursor above every window, so the small arrow stays visible
at the tip. Hiding it takes SetSystemCursor, which is system-wide and survives a crash.
ponytail: frames are drawn once at the first monitor's scale; re-render on a DPI change if a
mixed-scale setup looks wrong.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

log = logging.getLogger(__name__)

FRAME_MS = 16
HOLD_S = 0.18  # how long after the last move it counts as still moving
FRAMES = 12
MIN_SCALE = 0.35
RISE = 0.3
FALL = 0.2
BASE_HEIGHT = 132
_SUPERSAMPLE = 4
_YELLOW_TOP = (255, 222, 89, 255)
_YELLOW_BOTTOM = (255, 181, 30, 255)
_BLACK = (10, 10, 10, 255)
# The arrow, as fractions of its height, tip first.
_ARROW = [(0.0, 0.0), (0.0, 0.86), (0.2, 0.7), (0.34, 0.98), (0.52, 0.9), (0.38, 0.63), (0.66, 0.61)]
_DUCK_BOX = (0.03, 0.27, 0.45, 0.69)  # left, top, right, bottom as fractions of the height

_lock = threading.Lock()
_last_move = 0.0
_running = False
_failed = False
_pointer: _Pointer | None = None


def next_level(level: float, moving: bool) -> float:
    """One animation step of the 0..1 size: quick ease in, softer ease out, snaps at the ends."""
    target = 1.0 if moving else 0.0
    next_value = level + (target - level) * (RISE if moving else FALL)
    if next_value > 0.98:
        return 1.0
    return 0.0 if next_value < 0.02 else next_value


def frame_index(level: float) -> int:
    return max(0, min(FRAMES - 1, round(level * FRAMES) - 1))


def _arrow_mask(size: tuple[int, int], points: list[tuple[int, int]], stroke: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.polygon(points, fill=255)
    if stroke:
        draw.line([*points, points[0], points[1]], fill=255, width=stroke * 2, joint="curve")
        for x, y in points:
            draw.ellipse((x - stroke, y - stroke, x + stroke, y + stroke), fill=255)
    return mask


def render_pointer(height: int, duck: Image.Image | None) -> tuple[Image.Image, int]:
    """The full-size pointer and its margin: the arrow's tip sits at (margin, margin)."""
    ss = _SUPERSAMPLE
    margin = max(6, height // 7)
    tall = height * ss
    pad = margin * ss
    size = (int(height * 0.66) * ss + pad * 2, tall + pad * 2)
    points = [(pad + int(x * tall), pad + int(y * tall)) for x, y in _ARROW]

    corner = max(2, tall // 40)
    outline = max(3, tall // 26)
    black = _arrow_mask(size, points, corner + outline)
    body = _arrow_mask(size, points, corner)

    out = Image.new("RGBA", size, (0, 0, 0, 0))
    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 130), (0, tall // 40), black)
    out.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(tall / 45)))
    out.paste(_BLACK, (0, 0), black)

    gradient = Image.new("RGBA", (1, size[1]))
    for y in range(size[1]):
        t = min(1.0, max(0.0, (y - pad) / tall))
        gradient.putpixel((0, y), tuple(int(a + (b - a) * t) for a, b in zip(_YELLOW_TOP, _YELLOW_BOTTOM)))
    out.paste(gradient.resize(size), (0, 0), body)

    if duck is not None:
        left, top, right, bottom = (int(v * tall) for v in _DUCK_BOX)
        side = right - left
        icon = duck.convert("RGBA").resize((side, bottom - top), Image.LANCZOS)
        spot = (pad + left, pad + top)
        # A black edge lifts the yellow duck off the yellow arrow.
        rim = Image.new("RGBA", size, (0, 0, 0, 0))
        rim.paste(_BLACK, spot, icon.getchannel("A"))
        rim_alpha = rim.getchannel("A").filter(ImageFilter.MaxFilter(outline // 2 * 2 + 1))
        out.paste(_BLACK, (0, 0), rim_alpha)
        out.alpha_composite(icon, spot)

    final = out.resize((size[0] // ss, size[1] // ss), Image.LANCZOS)
    return final, margin


def render_frames(full: Image.Image, margin: int) -> list[Image.Image]:
    """``FRAMES`` steps of growing and fading in, the tip fixed at (margin, margin)."""
    frames: list[Image.Image] = []
    for step in range(1, FRAMES + 1):
        level = step / FRAMES
        scale = MIN_SCALE + (1.0 - MIN_SCALE) * level
        size = (max(1, int(full.width * scale)), max(1, int(full.height * scale)))
        small = full.resize(size, Image.LANCZOS)
        red, green, blue, alpha = small.split()
        small = Image.merge("RGBA", (red, green, blue, alpha.point(lambda a: int(a * level))))
        frame = Image.new("RGBA", full.size, (0, 0, 0, 0))
        frame.paste(small, (int(margin - margin * scale), int(margin - margin * scale)))
        frames.append(frame)
    return frames


def load_duck() -> Image.Image | None:
    """The app's real Ducky icon."""
    try:
        from frontend.tray_icon import open_png, resolve_status_png_path

        path = resolve_status_png_path("online")
        return open_png(path) if path else None
    except Exception:
        log.warning("trackpad pointer: Ducky icon unavailable", exc_info=True)
        return None


def _cursor_pos() -> tuple[int, int]:
    class Point(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    point = Point()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return int(point.x), int(point.y)


class _Pointer:
    def __init__(self, root: Any) -> None:
        from frontend.ui_web.window_spotlight_paint import LayeredWindow, dpi_scale

        self.root = root
        self.window = LayeredWindow(root, click_through=True)
        full, self.margin = render_pointer(int(BASE_HEIGHT * dpi_scale(self.window.hwnd)), load_duck())
        self.frames = render_frames(full, self.margin)
        self.window.show(False)
        self.level = 0.0
        self.painted = -1
        self.visible = False

    def tick(self) -> None:
        self.level = next_level(self.level, time.monotonic() - _last_move < HOLD_S)
        if self.level <= 0.0:
            self.window.show(False)
            self.visible = False
            self.painted = -1
            _finished()
            return
        x, y = _cursor_pos()
        left, top = x - self.margin, y - self.margin
        if not self.visible:
            self.window.show(True)
            self.window.raise_()
            self.visible = True
        frame = frame_index(self.level)
        if frame != self.painted:
            self.window.paint(self.frames[frame], left, top)
            self.painted = frame
        else:
            self.window.move(left, top)
        self.root.after(FRAME_MS, _safe_tick)


def _finished() -> None:
    global _running
    with _lock:
        _running = False


def _give_up(what: str) -> None:
    """A pointer effect must never get in the way of moving the mouse."""
    global _failed
    log.warning("trackpad pointer off: %s", what, exc_info=True)
    _failed = True
    _finished()


def _safe_tick() -> None:
    global _pointer
    try:
        if _pointer is not None:
            _pointer.tick()
    except Exception:
        _give_up("animation failed")
        if _pointer is not None:
            _pointer.window.destroy()
            _pointer = None


def _begin(root: Any) -> None:
    global _pointer
    try:
        if _pointer is None:
            _pointer = _Pointer(root)
        _safe_tick()
    except Exception:
        _give_up("could not start")


def _start() -> None:
    try:
        from frontend.ui_web.window_spotlight import _spotlight_root

        root = _spotlight_root()
        root.after(0, lambda: _begin(root))
    except Exception:
        _give_up("no overlay loop")


def nudge() -> None:
    """The trackpad just moved the mouse. Cheap: called for every move, on the input thread."""
    global _last_move, _running
    if sys.platform != "win32" or _failed:
        return
    _last_move = time.monotonic()
    with _lock:
        if _running:
            return
        _running = True
    threading.Thread(target=_start, daemon=True, name="trackpad-pointer").start()
