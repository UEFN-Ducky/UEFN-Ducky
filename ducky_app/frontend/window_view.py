"""Look at other desktop windows through the remote panel tunnel.

ponytail: WebRTC crops a getDisplayMedia screen track to this window
(primary-monitor assumption — fit_window moves it onto the work area and
keeps the viewer's aspect so the video fills the phone with no bars).
No JPEG path: a stream that cannot go peer-to-peer reports why. Covered
windows show whatever is on top; the stream brings the target to the
front so clicks hit the same pixels. Windows Graphics Capture if they need
occluded GPU windows. STUN-only — TURN is the upgrade for strict NATs.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

_KIND_ORDER = {"uefn": 0, "blender": 1, "app": 2}


def window_fit_size(
    width: int,
    height: int,
    max_w: int = 3840,
    max_h: int = 2160,
) -> tuple[int, int]:
    """Viewer pixels → window size: at least 400×300, scaled down to fit the
    work area while keeping the viewer's aspect (a portrait phone must not
    push the window off-screen — the capture only sees on-screen pixels)."""
    w = max(400, int(width or 0))
    h = max(300, int(height or 0))
    max_w = max(400, int(max_w or 0))
    max_h = max(300, int(max_h or 0))
    scale = min(1.0, max_w / w, max_h / h)
    if scale < 1.0:
        w = max(400, int(w * scale))
        h = max(300, int(h * scale))
    return min(w, max_w), min(h, max_h)


def kind_for(title: str, exe: str = "") -> str:
    blob = f"{title} {exe}".lower().replace("\\", "/")
    base = blob.rsplit("/", 1)[-1]
    if "unrealeditorfortnite" in blob or "unreal editor for fortnite" in blob:
        return "uefn"
    if "blender" in base or "blender" in blob:
        return "blender"
    return "app"


def list_window_views() -> list[dict[str, Any]]:
    if sys.platform != "win32":
        return []
    rows = _enum_windows()
    rows.sort(key=lambda r: (_KIND_ORDER.get(str(r.get("kind") or "app"), 9), str(r.get("title") or "").lower()))
    return rows


def window_box(hwnd: int) -> dict[str, int]:
    """On-screen rect plus virtual/primary metrics for the WebRTC crop."""
    if sys.platform != "win32" or hwnd <= 0:
        return {}
    box = _window_box(hwnd)
    if not box:
        return {}
    left, top, right, bottom = box
    sl, st, sw, sh, pw, ph = _screen_metrics()
    return {
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "screen_left": sl,
        "screen_top": st,
        "screen_w": sw,
        "screen_h": sh,
        "primary_w": pw,
        "primary_h": ph,
    }


def map_norm_to_screen(box: tuple[int, int, int, int], nx: float, ny: float) -> tuple[int, int]:
    left, top, right, bottom = box
    width = max(1, right - left)
    height = max(1, bottom - top)
    nx = 0.0 if nx < 0 else 1.0 if nx > 1 else float(nx)
    ny = 0.0 if ny < 0 else 1.0 if ny > 1 else float(ny)
    return left + int(nx * (width - 1)), top + int(ny * (height - 1))


def viewport_inset(
    box: tuple[int, int, int, int],
    *,
    top: float = 0.12,
    left: float = 0.18,
    right: float = 0.22,
    bottom: float = 0.08,
) -> tuple[int, int, int, int]:
    """Shrink a window box toward the UEFN 3D view (skip toolbars / side panels)."""
    l, t, r, b = box
    w = max(1, r - l)
    h = max(1, b - t)
    nl, nt, nr, nb = l + int(w * left), t + int(h * top), r - int(w * right), b - int(h * bottom)
    if nr - nl < 80 or nb - nt < 80:
        return box
    return nl, nt, nr, nb


def bring_to_front(hwnd: int) -> bool:
    if sys.platform != "win32" or hwnd <= 0 or _is_our_hwnd(hwnd):
        return False
    if _foreground_hwnd() == hwnd:
        return True
    return _raise_window(hwnd)


def set_window_topmost(hwnd: int, on: bool) -> bool:
    """Pin (or unpin) the window above all others for the stream's lifetime.

    getDisplayMedia captures on-screen pixels, so an always-on-top occluder
    (a pinned Discord, a media overlay) would cover the watched window even
    after we raise it. Holding the target TOPMOST while a viewer is connected
    guarantees the capture sees it; teardown clears the flag.
    """
    if sys.platform != "win32" or hwnd <= 0 or _is_our_hwnd(hwnd):
        return False
    import ctypes

    user32 = ctypes.windll.user32
    if not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    insert_after = -1 if on else -2  # HWND_TOPMOST / HWND_NOTOPMOST
    flags = 0x0001 | 0x0002 | 0x0010  # NOSIZE | NOMOVE | NOACTIVATE
    user32.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0, flags)
    if on:
        _raise_window(hwnd)
    return True


def fit_window(hwnd: int, width: int, height: int) -> bool:
    if sys.platform != "win32" or hwnd <= 0 or _is_our_hwnd(hwnd):
        return False
    box = _window_box(hwnd)
    if not box:
        return False
    left, top, right, bottom = box
    ox, oy, aw, ah = _primary_work_area()
    w, h = window_fit_size(width, height, aw, ah)
    if (
        abs((right - left) - w) < 8
        and abs((bottom - top) - h) < 8
        and abs(left - ox) < 8
        and abs(top - oy) < 8
    ):
        return False
    import ctypes

    # SWP_NOZORDER | SWP_NOACTIVATE — resize without a focus fight that hitchs UEFN.
    ctypes.windll.user32.SetWindowPos(hwnd, 0, int(ox), int(oy), int(w), int(h), 0x0014)
    # A just-fitted window can land behind whatever occupied that spot; the
    # capture would then stream the wrong window. Surface it once.
    if _foreground_hwnd() != hwnd:
        _raise_window(hwnd)
    return True


def inject_pointer(
    hwnd: int,
    kind: str,
    nx: float,
    ny: float,
    *,
    button: int = 0,
    delta: int = 0,
    dx: int | None = None,
    dy: int | None = None,
    look: bool = False,
) -> None:
    if sys.platform != "win32" or hwnd <= 0 or _is_our_hwnd(hwnd):
        return
    if look:
        _inject_look(hwnd, kind, button=button, dx=dx, dy=dy)
        return
    if kind == "move" and dx is not None:
        _send_mouse(0x0001, 0, int(dx), int(dy or 0))
        return
    box = _client_box(hwnd) or _window_box(hwnd)
    if not box:
        return
    if kind != "move":
        bring_to_front(hwnd)
    x, y = map_norm_to_screen(box, nx, ny)
    import ctypes

    user32 = ctypes.windll.user32
    user32.SetCursorPos(int(x), int(y))
    flags_down = {0: 0x0002, 1: 0x0020, 2: 0x0008}
    flags_up = {0: 0x0004, 1: 0x0040, 2: 0x0010}
    if kind == "move":
        return
    if kind == "wheel":
        _send_mouse(0x0800, int(delta) * 120)
        return
    flag = flags_down.get(int(button), 0x0002) if kind == "down" else flags_up.get(int(button), 0x0004)
    if kind in ("down", "up"):
        _send_mouse(flag, 0)


def inject_key(hwnd: int, key: str, *, down: bool = True) -> None:
    if sys.platform != "win32" or hwnd <= 0 or _is_our_hwnd(hwnd):
        return
    vk = _vk_for_key(key)
    if not vk:
        return
    bring_to_front(hwnd)
    _send_key(vk, down=down)


def handle_stream_message(hwnd: int, payload: bytes) -> None:
    try:
        event = json.loads(payload.decode("utf-8"))
    except Exception:
        return
    if not isinstance(event, dict):
        return
    kind = str(event.get("type") or "")
    if kind == "size":
        fit_window(hwnd, int(event.get("w") or 0), int(event.get("h") or 0))
        return
    if kind in ("down", "up", "move", "wheel"):
        rel = "dx" in event
        inject_pointer(
            hwnd,
            kind,
            float(event.get("x") or 0),
            float(event.get("y") or 0),
            button=int(event.get("button") or 0),
            delta=int(event.get("delta") or 0),
            dx=int(event.get("dx") or 0) if rel else None,
            dy=int(event.get("dy") or 0) if rel else None,
            look=bool(event.get("look")),
        )
        return
    if kind in ("key", "keydown", "keyup"):
        inject_key(hwnd, str(event.get("key") or ""), down=kind != "keyup")


def _enum_windows() -> list[dict[str, Any]]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    me = os.getpid()
    out: list[dict[str, Any]] = []

    def _text(hwnd: int) -> str:
        n = int(user32.GetWindowTextLengthW(hwnd) or 0)
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return (buf.value or "").strip()

    def _callback(hwnd, _lparam):
        hwnd = int(hwnd)
        if not user32.IsWindow(hwnd):
            return True
        iconic = bool(user32.IsIconic(hwnd))
        visible = bool(user32.IsWindowVisible(hwnd))
        if not visible and not iconic:
            return True
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) == me:
            return True
        exe = _exe_name(int(pid.value))
        title = _text(hwnd) or exe
        if not title:
            return True
        out.append(
            {
                "id": str(int(hwnd)),
                "title": title[:80],
                "kind": kind_for(title, exe),
            }
        )
        return True

    user32.EnumWindows(WNDENUMPROC(_callback), 0)
    return out


def _foreground_hwnd() -> int:
    if sys.platform != "win32":
        return 0
    import ctypes

    return int(ctypes.windll.user32.GetForegroundWindow() or 0)


def _raise_window(hwnd: int) -> bool:
    """Bring the target above every other window and give it input focus.

    getDisplayMedia captures the monitor's actual pixels, so a covered target
    streams whatever sits on top. Windows blocks SetForegroundWindow from a
    background process (foreground lock), so we (1) drop the lock timeout,
    (2) tap ALT to satisfy the "user gesture" rule, (3) AttachThreadInput to
    the current foreground thread, and (4) flip the window to TOPMOST then
    back — the Z-order flip alone un-occludes it even when focus is denied.
    """
    import ctypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    if not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE

    # Drop the foreground-lock timeout so SetForegroundWindow is honored.
    try:
        prev = ctypes.c_uint(0)
        user32.SystemParametersInfoW(0x2000, 0, ctypes.byref(prev), 0)  # SPI_GETFOREGROUNDLOCKTIMEOUT
        user32.SystemParametersInfoW(0x2001, 0, ctypes.c_void_p(0), 0)  # SPI_SETFOREGROUNDLOCKTIMEOUT
    except Exception:
        prev = None

    fg = int(user32.GetForegroundWindow() or 0)
    our_tid = int(kernel32.GetCurrentThreadId() or 0)
    fg_tid = int(user32.GetWindowThreadProcessId(fg, None) or 0) if fg else 0
    attached = bool(our_tid and fg_tid and our_tid != fg_tid and user32.AttachThreadInput(our_tid, fg_tid, True))
    try:
        # ALT tap unlocks SetForegroundWindow for background callers.
        _send_key(0x12, down=True)
        _send_key(0x12, down=False)
        # HWND_TOPMOST (-1) then HWND_NOTOPMOST (-2): raise Z-order without
        # leaving the window permanently pinned above everything.
        flags = 0x0001 | 0x0002 | 0x0010  # NOSIZE | NOMOVE | NOACTIVATE
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, flags)
        user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, flags)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(our_tid, fg_tid, False)
        if prev is not None:
            try:
                user32.SystemParametersInfoW(0x2001, 0, ctypes.c_void_p(prev.value), 0)
            except Exception:
                pass
    return True


def _primary_work_area() -> tuple[int, int, int, int]:
    """(left, top, width, height) of the primary monitor minus the taskbar."""
    if sys.platform != "win32":
        return 0, 0, 1920, 1080
    import ctypes
    from ctypes import wintypes

    rect = wintypes.RECT()
    if not ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
        return 0, 0, 1920, 1080
    return (
        int(rect.left),
        int(rect.top),
        max(400, int(rect.right - rect.left)),
        max(300, int(rect.bottom - rect.top)),
    )


def _screen_metrics() -> tuple[int, int, int, int, int, int]:
    if sys.platform != "win32":
        return 0, 0, 1920, 1080, 1920, 1080
    import ctypes

    user32 = ctypes.windll.user32
    return (
        int(user32.GetSystemMetrics(76)),
        int(user32.GetSystemMetrics(77)),
        int(user32.GetSystemMetrics(78) or 1920),
        int(user32.GetSystemMetrics(79) or 1080),
        int(user32.GetSystemMetrics(0) or 1920),
        int(user32.GetSystemMetrics(1) or 1080),
    )


def _window_box(hwnd: int) -> tuple[int, int, int, int] | None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
        return None
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)


def _client_box(hwnd: int) -> tuple[int, int, int, int] | None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
        return None
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    pt = wintypes.POINT(int(rect.left), int(rect.top))
    if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    return int(pt.x), int(pt.y), int(pt.x + rect.right), int(pt.y + rect.bottom)


def _area(box: tuple[int, int, int, int]) -> int:
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def _largest_child_box(hwnd: int) -> tuple[int, int, int, int] | None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    boxes: list[tuple[int, int, int, int]] = []

    def _cb(child: int, _lp: int) -> bool:
        if not user32.IsWindowVisible(child):
            return True
        box = _client_box(int(child))
        if box:
            boxes.append(box)
        return True

    cb = WNDENUMPROC(_cb)
    user32.EnumChildWindows(hwnd, cb, 0)
    return max(boxes, key=_area) if boxes else None


def _look_box(hwnd: int) -> tuple[int, int, int, int] | None:
    parent = _client_box(hwnd) or _window_box(hwnd)
    if not parent:
        return None
    child = _largest_child_box(hwnd)
    pa = _area(parent)
    if child and pa and _area(child) < 0.85 * pa:
        return viewport_inset(child, top=0.04, left=0.04, right=0.04, bottom=0.04)
    return viewport_inset(parent)


_look_clipped = False


def release_look_capture() -> None:
    global _look_clipped
    if sys.platform != "win32":
        _look_clipped = False
        return
    try:
        import ctypes

        ctypes.windll.user32.ClipCursor(None)
    except Exception:
        pass
    _look_clipped = False


def _clip_look(box: tuple[int, int, int, int]) -> None:
    global _look_clipped
    import ctypes
    from ctypes import wintypes

    rect = wintypes.RECT(int(box[0]), int(box[1]), int(box[2]), int(box[3]))
    ctypes.windll.user32.ClipCursor(ctypes.byref(rect))
    _look_clipped = True


def _post_rbutton(screen_x: int, screen_y: int, *, down: bool) -> None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    pt = wintypes.POINT(int(screen_x), int(screen_y))
    target = int(user32.WindowFromPoint(pt) or 0)
    if not target:
        return
    user32.ScreenToClient(target, ctypes.byref(pt))
    msg = 0x0204 if down else 0x0205
    user32.PostMessageW(target, msg, 0x0002, (int(pt.y) << 16) | (int(pt.x) & 0xFFFF))


def _inject_look(hwnd: int, kind: str, *, button: int, dx: int | None, dy: int | None) -> None:
    import ctypes

    box = _look_box(hwnd)
    if not box:
        return
    user32 = ctypes.windll.user32
    cx, cy = map_norm_to_screen(box, 0.5, 0.5)
    if kind == "move" and dx is not None:
        _send_mouse(0x0001, 0, int(dx), int(dy or 0))
        user32.SetCursorPos(int(cx), int(cy))
        return
    bring_to_front(hwnd)
    user32.SetCursorPos(int(cx), int(cy))
    if kind == "down":
        _clip_look(box)
        _send_mouse(0x0008 if int(button) == 2 else 0x0002, 0)
        _post_rbutton(cx, cy, down=True)
        return
    if kind == "up":
        _send_mouse(0x0010 if int(button) == 2 else 0x0004, 0)
        _post_rbutton(cx, cy, down=False)
        release_look_capture()


def _hwnd_pid(hwnd: int) -> int:
    import ctypes
    from ctypes import wintypes

    pid = wintypes.DWORD(0)
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _is_our_hwnd(hwnd: int) -> bool:
    try:
        return _hwnd_pid(hwnd) == os.getpid()
    except Exception:
        return True


def _input_structs():
    import ctypes

    extra = ctypes.c_ulong(0)

    class Mouse(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.c_long),
            ("dy", ctypes.c_long),
            ("mouseData", ctypes.c_ulong),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class Key(ctypes.Structure):
        _fields_ = [
            ("wVk", ctypes.c_ushort),
            ("wScan", ctypes.c_ushort),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class Union(ctypes.Union):
        _fields_ = [("mi", Mouse), ("ki", Key)]

    class Input(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("union", Union)]

    return ctypes, extra, Mouse, Key, Input


def _send_mouse(flags: int, data: int, dx: int = 0, dy: int = 0) -> None:
    ctypes, extra, Mouse, _Key, Input = _input_structs()
    inp = Input()
    inp.type = 0
    inp.union.mi = Mouse(int(dx), int(dy), int(data) & 0xFFFFFFFF, int(flags), 0, ctypes.pointer(extra))
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(Input))


def _send_key(vk: int, *, down: bool) -> None:
    ctypes, extra, _Mouse, Key, Input = _input_structs()
    inp = Input()
    inp.type = 1
    inp.union.ki = Key(int(vk) & 0xFFFF, 0, 0 if down else 0x0002, 0, ctypes.pointer(extra))
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(Input))


_NAMED_VK = {
    "Enter": 0x0D,
    "Tab": 0x09,
    "Backspace": 0x08,
    "Escape": 0x1B,
    "Esc": 0x1B,
    " ": 0x20,
    "Space": 0x20,
    "ArrowLeft": 0x25,
    "ArrowUp": 0x26,
    "ArrowRight": 0x27,
    "ArrowDown": 0x28,
    "Delete": 0x2E,
    "Home": 0x24,
    "End": 0x23,
    "PageUp": 0x21,
    "PageDown": 0x22,
    "Shift": 0x10,
    "Control": 0x11,
    "Alt": 0x12,
    "Meta": 0x5B,
    "CapsLock": 0x14,
}


def _vk_for_key(key: str) -> int:
    if not key:
        return 0
    if key in _NAMED_VK:
        return _NAMED_VK[key]
    if key.startswith("F") and key[1:].isdigit():
        n = int(key[1:])
        if 1 <= n <= 24:
            return 0x70 + n - 1
    if len(key) == 1:
        if sys.platform != "win32":
            return ord(key.upper())
        import ctypes

        scan = int(ctypes.windll.user32.VkKeyScanW(ord(key)))
        return scan & 0xFF if scan != -1 else 0
    return 0


def _exe_name(pid: int) -> str:
    if pid <= 0:
        return ""
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(260)
        buf = ctypes.create_unicode_buffer(260)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        path = buf.value or ""
        return path.replace("\\", "/").rsplit("/", 1)[-1]
    finally:
        kernel32.CloseHandle(handle)
