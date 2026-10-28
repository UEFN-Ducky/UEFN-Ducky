"""Desktop spotlight: dim every monitor, leave a hole on one control, explain it.

UEFN draws its own UI, so nothing can list its buttons. The caller passes a box as
fractions of a window screenshot. This overlay follows that window across monitors
and resizes by keeping the box pinned to the nearest corner.

The dim layer catches every click. The hole is fully transparent pixels of that
layered window, so a click there reaches the real control. Esc and the card's
close button always get out. The pixels are painted in ``window_spotlight_paint``
to match the in-app Show me popup.
``ponytail:`` keys typed into the target app are not blocked. A low-level
keyboard hook (``SetWindowsHookEx WH_KEYBOARD_LL``) would, if that ever matters.
``ponytail:`` corner pinning is wrong for a panel in the middle of a resized
layout. Re-capture and re-aim on resize when that shows up.
"""

from __future__ import annotations

import ctypes
import io
import re
import sys
import threading
from typing import Any

_DIM_ALPHA = 140  # rgba(0, 0, 0, 0.55), same as the in-app spotlight
_GAP = 12
_POLL_MS = 50
_MIN_PX = 8

_current: Spotlight | None = None
_tk_root: Any = None
_loop_lock = threading.Lock()
_loop_ready = threading.Event()
_loop_root: Any = None
_loop_started = False


def bind_root(root: Any) -> None:
    """The panel's Tk root. ``bind_tk_root`` on the panel API calls this."""
    global _tk_root
    _tk_root = root


def clean_box(raw: Any) -> dict[str, float] | None:
    """``{x, y, w, h}`` fractions of a window, clamped into 0..1. None if unusable."""
    if not isinstance(raw, dict):
        return None
    try:
        x, y, w, h = (float(raw[key]) for key in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None

    def clamp(value: float) -> float:
        return min(1.0, max(0.0, value))

    w, h = clamp(w), clamp(h)
    if w <= 0 or h <= 0:
        return None
    x, y = clamp(x), clamp(y)
    if x + w > 1:
        x = 1.0 - w
    if y + h > 1:
        y = 1.0 - h
    return {"x": x, "y": y, "w": w, "h": h}


def anchor_box(box: dict[str, float], win_w: int, win_h: int) -> dict[str, Any]:
    """Pin a fraction box to the nearest window corner, in pixels.

    A toolbar stays on the top-left and a side panel on the right edge when the
    window is resized, instead of stretching with it.
    """
    width = max(int(win_w), 1)
    height = max(int(win_h), 1)
    x = float(box["x"]) * width
    y = float(box["y"]) * height
    w = max(float(_MIN_PX), float(box["w"]) * width)
    h = max(float(_MIN_PX), float(box["h"]) * height)
    if x + w > width:
        x = max(0.0, width - w)
    if y + h > height:
        y = max(0.0, height - h)
    if x + w / 2 <= width / 2:
        ax, ox = "left", x
    else:
        ax, ox = "right", width - (x + w)
    if y + h / 2 <= height / 2:
        ay, oy = "top", y
    else:
        ay, oy = "bottom", height - (y + h)
    return {"ax": ax, "ay": ay, "ox": float(ox), "oy": float(oy), "w": float(w), "h": float(h)}


def project_box(
    anchored: dict[str, Any], left: int, top: int, win_w: int, win_h: int
) -> tuple[int, int, int, int]:
    """Screen rect of an anchored box for a window now at ``left, top`` of ``win_w x win_h``."""
    w = max(int(round(float(anchored["w"]))), _MIN_PX)
    h = max(int(round(float(anchored["h"]))), _MIN_PX)
    width = max(int(win_w), w)
    height = max(int(win_h), h)
    if anchored.get("ax") == "right":
        x = left + width - w - float(anchored["ox"])
    else:
        x = left + float(anchored["ox"])
    if anchored.get("ay") == "bottom":
        y = top + height - h - float(anchored["oy"])
    else:
        y = top + float(anchored["oy"])
    x = min(max(left, int(round(x))), left + width - w)
    y = min(max(top, int(round(y))), top + height - h)
    return x, y, w, h


def _overlaps(
    x: int, y: int, w: int, h: int, hx: int, hy: int, hw: int, hh: int
) -> bool:
    return x < hx + hw and x + w > hx and y < hy + hh and y + h > hy


def place_card(
    hole: tuple[int, int, int, int],
    monitor: tuple[int, int, int, int],
    card_w: int,
    card_h: int,
    gap: int = _GAP,
) -> tuple[int, int]:
    """Put the card beside the hole, on the same monitor, without covering it."""
    hx, hy, hw, hh = hole
    left, top, right, bottom = monitor
    card_w = max(int(card_w), 1)
    card_h = max(int(card_h), 1)

    def fit(x: int, y: int) -> tuple[int, int]:
        return (
            max(left, min(int(x), right - card_w)),
            max(top, min(int(y), bottom - card_h)),
        )

    candidates = (
        (hx + hw + gap, hy),
        (hx - gap - card_w, hy),
        (hx, hy + hh + gap),
        (hx, hy - gap - card_h),
    )
    fitted = [fit(x, y) for x, y in candidates]
    for x, y in fitted:
        if not _overlaps(x, y, card_w, card_h, hx, hy, hw, hh):
            return x, y
    return fitted[0]


def pad_hole(x: int, y: int, w: int, h: int) -> tuple[int, int, int, int, int]:
    """Same hole as the in-app spotlight: 8px of air, rounded 8, a circle on a small control."""
    circle = w <= 44 and h <= 44
    x -= 8
    y -= 8
    w = max(24, w + 16)
    h = max(24, h + 16)
    if circle:
        size = max(w, h)
        x += (w - size) / 2
        y += (h - size) / 2
        size = int(size)
        return int(round(x)), int(round(y)), size, size, size // 2
    return int(round(x)), int(round(y)), int(w), int(h), 12


def resolve_window(spec: str) -> dict[str, Any]:
    """``uefn``, a window title regex, or an hwnd. The largest unowned match wins."""
    name = (spec or "uefn").strip()
    if name.isdigit():
        return _window_info(int(name))
    if name.lower() == "uefn":
        from backend.tools.core.uefn_windows import list_uefn_windows

        listed = list_uefn_windows()
        wins = [row for row in listed.get("windows") or [] if row.get("rect")]
        if not wins:
            return {"ok": False, "error": "UEFN is not open"}
        return {"ok": True, **_largest(wins)}
    try:
        pat = re.compile(name, re.I)
    except re.error as exc:
        return {"ok": False, "error": f"bad window pattern: {exc}"}
    hits = [row for row in _list_top_windows() if pat.search(str(row.get("title") or ""))]
    if not hits:
        return {"ok": False, "error": f"no window matching {name!r}"}
    return {"ok": True, **_largest(hits)}


def prepare_window_show(steps: list[dict[str, Any]], default_window: str = "uefn") -> dict[str, Any]:
    """Resolve the window, pin each box, and screenshot the first box for the agent."""
    if not steps:
        return {"error": "nothing to show"}
    cache: dict[str, dict[str, Any]] = {}
    prepared: list[dict[str, Any]] = []
    for step in steps:
        spec = str(step.get("window") or default_window or "uefn").strip() or "uefn"
        found = cache.get(spec)
        if found is None:
            found = resolve_window(spec)
            cache[spec] = found
        if not found.get("ok"):
            return {"error": str(found.get("error") or "window not found")}
        rect = found.get("rect") or {}
        anchor = anchor_box(step["box"], int(rect.get("width") or 0), int(rect.get("height") or 0))
        prepared.append(
            {
                "title": str(step.get("title") or ""),
                "body": str(step.get("body") or ""),
                "click": bool(step.get("click")),
                "hwnd": int(found["hwnd"]),
                "anchor": anchor,
            }
        )
    first = cache[str(steps[0].get("window") or default_window or "uefn").strip() or "uefn"]
    preview = _annotate(int(first["hwnd"]), steps[0]["box"])
    label = str(first.get("title") or default_window or "uefn")
    return {
        "rpc": {"hwnd": int(first["hwnd"]), "window": label, "steps": prepared},
        "preview": preview,
    }


def _spotlight_root() -> Any:
    """A Tk mainloop that this process owns.

    The panel's root was created on the UI thread, which is stuck inside the
    webview message loop, so ``after`` from anywhere else raises "main thread
    is not in main loop". A second interpreter on its own thread can show the
    overlay. ``ponytail:`` one extra Tcl interpreter for the life of the app.
    """
    global _loop_root, _loop_started
    with _loop_lock:
        if not _loop_started:
            _loop_started = True

            def run() -> None:
                global _loop_root
                import tkinter as tk

                root = tk.Tk()
                root.withdraw()

                def mark() -> None:
                    global _loop_root
                    _loop_root = root
                    _loop_ready.set()

                root.after(0, mark)
                root.mainloop()

            threading.Thread(target=run, name="spotlight-tk", daemon=True).start()
    if not _loop_ready.wait(8):
        raise RuntimeError("desktop spotlight did not start")
    if _loop_root is None:
        raise RuntimeError("desktop spotlight did not start")
    return _loop_root


def begin_window_show(request_id: str, params: dict[str, Any]) -> None:
    """Show the overlay on its Tk thread. Answers ``request_id`` when up, or on close if ``wait``."""
    from frontend.ui_web import ui_rpc

    if sys.platform != "win32":
        ui_rpc.respond(request_id, {"ok": False, "error": "desktop spotlight needs the Windows app"})
        return
    try:
        root = _spotlight_root()
    except Exception as exc:
        ui_rpc.respond(request_id, {"ok": False, "error": str(exc)})
        return

    def go() -> None:
        try:
            Spotlight.open(root, request_id, params)
        except Exception as exc:
            ui_rpc.respond(request_id, {"ok": False, "error": str(exc)})

    try:
        root.after(0, go)
    except Exception as exc:
        ui_rpc.respond(request_id, {"ok": False, "error": str(exc)})


def _largest(wins: list[dict[str, Any]]) -> dict[str, Any]:
    unowned = [row for row in wins if not row.get("owned")] or wins

    def area(row: dict[str, Any]) -> int:
        rect = row.get("rect") or {}
        return int(rect.get("width") or 0) * int(rect.get("height") or 0)

    return max(unowned, key=area)


def _window_info(hwnd: int) -> dict[str, Any]:
    from frontend.window_view import _window_box

    box = _window_box(int(hwnd))
    if not box:
        return {"ok": False, "error": f"window {hwnd} is not visible"}
    left, top, right, bottom = box
    return {
        "ok": True,
        "hwnd": int(hwnd),
        "title": _window_title(hwnd),
        "rect": {
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
            "width": right - left,
            "height": bottom - top,
        },
        "owned": False,
    }


def _window_title(hwnd: int) -> str:
    user32 = ctypes.windll.user32
    length = int(user32.GetWindowTextLengthW(hwnd) or 0)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _list_top_windows() -> list[dict[str, Any]]:
    from frontend.window_view import _window_box

    user32 = ctypes.windll.user32
    rows: list[dict[str, Any]] = []

    def each(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        box = _window_box(int(hwnd))
        if not box:
            return True
        left, top, right, bottom = box
        title = _window_title(int(hwnd))
        if not title:
            return True
        rows.append(
            {
                "hwnd": int(hwnd),
                "title": title,
                "owned": bool(user32.GetWindow(hwnd, 4)),
                "rect": {
                    "left": left,
                    "top": top,
                    "right": right,
                    "bottom": bottom,
                    "width": right - left,
                    "height": bottom - top,
                },
            }
        )
        return True

    from ctypes import wintypes

    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    callback = proto(each)
    user32.EnumWindows(callback, 0)
    return rows


def _annotate(hwnd: int, box: dict[str, float]) -> dict[str, Any]:
    """Screenshot the window with the box drawn, so the agent can check its aim."""
    from frontend.window_view import _window_box

    rect = _window_box(int(hwnd))
    if not rect:
        return {"capture_error": "window is not visible"}
    try:
        from PIL import ImageDraw, ImageGrab
    except Exception as exc:
        return {"capture_error": f"PIL unavailable: {exc}"}
    try:
        image = ImageGrab.grab(bbox=rect, all_screens=True)
    except TypeError:
        image = ImageGrab.grab(bbox=rect)
    except Exception as exc:
        return {"capture_error": str(exc)}
    width, height = image.size
    x = int(box["x"] * width)
    y = int(box["y"] * height)
    w = max(2, int(box["w"] * width))
    h = max(2, int(box["h"] * height))
    ImageDraw.Draw(image).rectangle((x, y, x + w, y + h), outline=(124, 92, 255), width=4)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    from frontend.ui_web.tool_captures import save_capture_for_agents

    saved = save_capture_for_agents(buf.getvalue(), prefix="spotlight")
    if not saved.get("ok"):
        return {"capture_error": str(saved.get("error") or "screenshot was not saved")}
    return {
        "path": str(saved.get("path") or ""),
        "capture_path": str(saved.get("capture_path") or saved.get("path") or ""),
        "media_url": saved.get("media_url") or "",
    }


def _emit(trigger_id: str, payload: dict[str, Any]) -> None:
    """Workflows listen for these. Off the Tk thread: a workflow must not freeze the overlay."""

    def run() -> None:
        try:
            from backend.automations.runner import emit_trigger

            emit_trigger(trigger_id, payload)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()


def _virtual_origin() -> tuple[int, int, int, int]:
    if sys.platform != "win32":
        return 0, 0, 1920, 1080
    user32 = ctypes.windll.user32
    return tuple(int(user32.GetSystemMetrics(i)) for i in (76, 77, 78, 79))  # type: ignore[return-value]


def _monitor_of(x: int, y: int) -> tuple[int, int, int, int]:
    """Monitor rectangle containing a point. Falls back to the virtual screen."""
    left, top, width, height = _virtual_origin()
    fallback = (left, top, left + width, top + height)
    try:
        from ctypes import wintypes

        class _POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class _RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        class _MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_ulong),
                ("rcMonitor", _RECT),
                ("rcWork", _RECT),
                ("dwFlags", ctypes.c_ulong),
            ]

        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.argtypes = [_POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HMONITOR
        user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        monitor = user32.MonitorFromPoint(_POINT(int(x), int(y)), 2)
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return fallback
        rect = info.rcMonitor
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    except Exception:
        return fallback


def _key_down(vk: int) -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def _button_state(vk: int = 0x01) -> tuple[bool, bool]:
    """(held now, pressed at any moment since the previous call) for a mouse button."""
    state = int(ctypes.windll.user32.GetAsyncKeyState(vk))
    return bool(state & 0x8000), bool(state & 0x0001)


def click_in_hole(armed: bool, was_down: bool, down: bool, pressed_since: bool, in_hole: bool) -> tuple[bool, bool]:
    """Decide whether the user just clicked the highlight. Returns (advance, armed).

    A press seen inside the hole arms the step and the release inside it advances, so
    the whole click reaches the real control first. A quick click can begin and end
    between two polls: ``pressed_since`` (the button was pressed since the last poll)
    with the pointer in the hole counts as that click.
    """
    if down and not was_down and in_hole:
        armed = True
    if armed and not down:
        return in_hole, False
    if not down and not was_down and pressed_since and in_hole:
        return True, False
    return False, armed


def _cursor() -> tuple[int, int]:
    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = _POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


class Spotlight:
    """One spotlight. A new one replaces the one on screen.

    Three per-pixel windows (``window_spotlight_paint``): the dim with its transparent
    hole, the ring, and the card. They are re-raised in that order every tick, on their
    real top-level handles, so the card is always above the dim and takes the clicks.
    """

    @classmethod
    def open(cls, root: Any, request_id: str, params: dict[str, Any]) -> None:
        global _current
        if _current is not None:
            _current._finish("replaced")
        spot = cls(root, request_id, params)
        _current = spot
        try:
            spot._build()
        except Exception:
            if _current is spot:
                _current = None
            raise

    def __init__(self, root: Any, request_id: str, params: dict[str, Any]) -> None:
        from frontend.ui_web.window_spotlight_paint import CardPixels, CardState

        self.root = root
        self.request_id = request_id
        self.wait = bool(params.get("wait"))
        self.window_label = str(params.get("window") or "")
        self.steps = [row for row in (params.get("steps") or []) if isinstance(row, dict)]
        self.index = 0
        self._answered = False
        self._after: str | None = None
        self._was_down = _key_down(0x01)
        self._arm = False
        self._visible = True
        self._hole = (0, 0, 1, 1)
        self._scale = 1.0
        self._state = CardState()
        self._pixels: CardPixels | None = None
        self._card_key: tuple[Any, ...] | None = None
        self._card_at: tuple[Any, ...] | None = None
        self._ring_margin = 0
        self._dim_key: tuple[int, ...] | None = None
        self._ring_key: tuple[Any, ...] | None = None
        self._pressed: str | None = None
        self.dim: Any = None
        self.ring: Any = None
        self.card: Any = None

    def _build(self) -> None:
        if not self.steps:
            self._answer({"ok": False, "error": "nothing to show"})
            return
        from frontend.ui_web.window_spotlight_paint import LayeredWindow

        self.dim = LayeredWindow(self.root)
        self.ring = LayeredWindow(self.root, click_through=True)
        self.card = LayeredWindow(self.root)
        self.card.bind("<Motion>", self._on_motion)
        self.card.bind("<Leave>", self._on_leave)
        self.card.bind("<Button-1>", self._on_press)
        self.card.bind("<ButtonRelease-1>", self._on_release)
        self._show_step(0)
        self._tick()

    def _step(self) -> dict[str, Any]:
        return self.steps[self.index]

    def _show_step(self, index: int, *, clicked: bool = False) -> None:
        self.index = index
        self._arm = False
        step = self._step()
        total = len(self.steps)
        self._state.title = str(step.get("title") or "")
        self._state.body = str(step.get("body") or "")[:600]
        self._state.index = index
        self._state.total = total
        self._state.click = bool(step.get("click"))
        self._state.hover = None
        self._pressed = None
        self._card_key = None
        self._layout()
        _emit(
            "spotlight.step",
            {"step": index + 1, "total": total, "clicked": clicked, "window": self.window_label},
        )
        if not self.wait:
            self._answer(self._payload(shown=True))

    def _layout(self) -> None:
        from frontend.window_view import _window_box
        from frontend.ui_web.window_spotlight_paint import clear_rect, dpi_scale, render_card, render_dim, render_ring

        if self.dim is None or self.ring is None or self.card is None:
            return
        step = self._step()
        hwnd = int(step.get("hwnd") or 0)
        box = _window_box(hwnd)
        if not box:
            self._set_visible(False)
            return
        self._set_visible(True)
        left, top, right, bottom = box
        hx, hy, hw, hh = project_box(step.get("anchor") or {}, left, top, right - left, bottom - top)
        hx, hy, hw, hh, radius = pad_hole(hx, hy, hw, hh)
        self._hole = (hx, hy, hw, hh)
        self._scale = dpi_scale(hwnd)

        vx, vy, vw, vh = _virtual_origin()
        dim_key = (vx, vy, vw, vh, hx, hy, hw, hh, radius)
        if dim_key != self._dim_key:
            self.dim.paint(render_dim(vw, vh, (hx - vx, hy - vy, hw, hh), radius), vx, vy)
            self._dim_key = dim_key

        ring_key = (hw, hh, radius, round(self._scale, 3))
        if ring_key != self._ring_key:
            image, margin = render_ring(hw, hh, radius, self._scale)
            self._ring_margin = margin
            self.ring.paint(image, hx - margin, hy - margin)
            self._ring_key = ring_key
        else:
            self.ring.move(hx - self._ring_margin, hy - self._ring_margin)

        card_key = (
            self._state.title, self._state.body, self._state.index, self._state.total,
            self._state.click, self._state.hover, round(self._scale, 3),
        )
        if card_key != self._card_key or self._pixels is None:
            self._pixels = render_card(self._state, self._scale)
            self._card_key = card_key
            self._card_at = None
        cw, ch = self._pixels.visible
        cx, cy = place_card(self._hole, _monitor_of(hx + hw // 2, hy + hh // 2), cw, ch)
        pad_l, pad_t, _pr, _pb = self._pixels.pad
        wx, wy = cx - pad_l, cy - pad_t
        # The card's shadow must never sit on the hole: a click there belongs to the real control.
        card_at = (wx, wy, self._hole)
        if card_at != self._card_at:
            self._card_at = card_at
            image = clear_rect(self._pixels.image, (hx - wx - 2, hy - wy - 2, hx - wx + hw + 2, hy - wy + hh + 2))
            self.card.paint(image, wx, wy)
        for window in (self.dim, self.ring, self.card):
            window.raise_()

    def _hit(self, event: Any) -> str | None:
        if self._pixels is None:
            return None
        try:
            return self._pixels.hit(int(event.x), int(event.y))
        except Exception:
            return None

    def _set_hover(self, name: str | None) -> None:
        if name == self._state.hover:
            return
        self._state.hover = name
        if self.card is not None:
            self.card.set_cursor("hand2" if name else "")
        self._layout()

    def _on_motion(self, event: Any) -> None:
        self._set_hover(self._hit(event))

    def _on_leave(self, _event: Any) -> None:
        self._set_hover(None)

    def _on_press(self, event: Any) -> None:
        self._pressed = self._hit(event)

    def _on_release(self, event: Any) -> None:
        target = self._hit(event)
        pressed, self._pressed = self._pressed, None
        if target is None or target != pressed:
            return
        if target == "close":
            self._on_close()
        elif target == "next":
            if self.index >= len(self.steps) - 1:
                self._on_close()
            else:
                self._on_next()
        elif target == "back":
            self._on_back()

    def _set_visible(self, visible: bool) -> None:
        if visible == self._visible:
            return
        self._visible = visible
        for window in (self.dim, self.ring, self.card):
            if window is not None:
                window.show(visible)

    def _tick(self) -> None:
        if _current is not self:
            return
        if _key_down(0x1B):
            self._finish("esc")
            return
        down, pressed_since = _button_state(0x01)
        # Advance on mouse-up so the whole click reaches the real control before the hole moves.
        if self._visible and bool(self._step().get("click")):
            x, y = _cursor()
            hx, hy, hw, hh = self._hole
            in_hole = hx <= x <= hx + hw and hy <= y <= hy + hh
            advance, self._arm = click_in_hole(self._arm, self._was_down, down, pressed_since, in_hole)
            if advance:
                self._was_down = False
                self._advance(clicked=True)
                return
        else:
            self._arm = False
        self._was_down = down
        self._layout()
        if _current is self and self.dim is not None:
            self._after = self.dim.top.after(_POLL_MS, self._tick)

    def _advance(self, *, clicked: bool) -> None:
        if self.index + 1 >= len(self.steps):
            self._finish("done")
            return
        self._show_step(self.index + 1, clicked=clicked)
        self._tick()

    def _on_next(self) -> None:
        if bool(self._step().get("click")):
            return
        if self.index + 1 >= len(self.steps):
            self._finish("done")
            return
        self._show_step(self.index + 1)

    def _on_back(self) -> None:
        if self.index:
            self._show_step(self.index - 1)

    def _on_close(self) -> None:
        last = self.index >= len(self.steps) - 1
        self._finish("done" if last else "close")

    def _payload(self, *, shown: bool, reason: str = "") -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": True,
            "shown": shown,
            "hwnd": int(self._step().get("hwnd") or 0),
            "window": self.window_label,
            "step": self.index + 1,
            "steps": len(self.steps),
        }
        if reason:
            out["reason"] = reason
        return out

    def _answer(self, payload: dict[str, Any]) -> None:
        if self._answered:
            return
        self._answered = True
        from frontend.ui_web import ui_rpc

        ui_rpc.respond(self.request_id, payload)

    def _finish(self, reason: str) -> None:
        global _current
        if _current is self:
            _current = None
        if self._after and self.dim is not None:
            try:
                self.dim.top.after_cancel(self._after)
            except Exception:
                pass
            self._after = None
        _emit(
            "spotlight.closed",
            {
                "reason": reason,
                "step": self.index + 1,
                "total": len(self.steps),
                "window": self.window_label,
            },
        )
        if self.wait or not self._answered:
            self._answer(self._payload(shown=reason in ("done", "close", "esc"), reason=reason))
        for window in (self.card, self.ring, self.dim):
            if window is not None:
                window.destroy()
        self.dim = self.ring = self.card = None
