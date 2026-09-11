"""Look at other desktop windows through the remote panel tunnel.

ponytail: grab the window's on-screen rectangle (Pillow ImageGrab) and
SendInput. Covered windows show whatever is on top; the stream brings the
target to the front so clicks hit the same pixels. Windows Graphics Capture
if they need occluded GPU windows.
"""

from __future__ import annotations

import io
import json
import os
import sys
from typing import Any

_MAX_EDGE = 1280
_KIND_ORDER = {"uefn": 0, "blender": 1, "app": 2}


def window_fit_size(width: int, height: int) -> tuple[int, int]:
    w = max(400, min(int(width or 0), 3840))
    h = max(300, min(int(height or 0), 2160))
    return w, h


def kind_for(title: str, exe: str = "") -> str:
    blob = f"{title} {exe}".lower().replace("\\", "/")
    base = blob.rsplit("/", 1)[-1]
    if "unrealeditorfortnite" in blob or "unreal editor for fortnite" in blob:
        return "uefn"
    if "blender" in base or "blender" in blob:
        return "blender"
    return "app"


def jpeg_bytes(image: Any, *, max_edge: int = _MAX_EDGE) -> bytes:
    from PIL import Image

    img = image
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    edge = max(w, h)
    if edge > max_edge and edge > 0:
        scale = max_edge / edge
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.BILINEAR)
    buf = io.BytesIO()
    # ponytail: skip optimize=True (second Huffman pass) — encode speed for 24fps.
    img.save(buf, format="JPEG", quality=50)
    return buf.getvalue()


def list_window_views() -> list[dict[str, Any]]:
    if sys.platform != "win32":
        return []
    rows = _enum_windows()
    rows.sort(key=lambda r: (_KIND_ORDER.get(str(r.get("kind") or "app"), 9), str(r.get("title") or "").lower()))
    return rows


def capture_window_jpeg(hwnd: int) -> bytes:
    if sys.platform != "win32" or hwnd <= 0:
        return b""
    box = _window_box(hwnd)
    if not box:
        return b""
    from PIL import ImageGrab

    grabbed = ImageGrab.grab(bbox=box, all_screens=True)
    if grabbed is None:
        return b""
    return jpeg_bytes(grabbed)


def map_norm_to_screen(box: tuple[int, int, int, int], nx: float, ny: float) -> tuple[int, int]:
    left, top, right, bottom = box
    width = max(1, right - left)
    height = max(1, bottom - top)
    nx = 0.0 if nx < 0 else 1.0 if nx > 1 else float(nx)
    ny = 0.0 if ny < 0 else 1.0 if ny > 1 else float(ny)
    return left + int(nx * (width - 1)), top + int(ny * (height - 1))


def bring_to_front(hwnd: int) -> bool:
    if sys.platform != "win32" or hwnd <= 0 or _is_our_hwnd(hwnd):
        return False
    import ctypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    if not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    fg = int(user32.GetForegroundWindow() or 0)
    our_tid = int(kernel32.GetCurrentThreadId() or 0)
    fg_tid = int(user32.GetWindowThreadProcessId(fg, None) or 0) if fg else 0
    if our_tid and fg_tid and our_tid != fg_tid:
        user32.AttachThreadInput(our_tid, fg_tid, True)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if our_tid and fg_tid and our_tid != fg_tid:
            user32.AttachThreadInput(our_tid, fg_tid, False)
    return True


def fit_window(hwnd: int, width: int, height: int) -> bool:
    if sys.platform != "win32" or hwnd <= 0 or _is_our_hwnd(hwnd):
        return False
    box = _window_box(hwnd)
    if not box:
        return False
    left, top, right, bottom = box
    w, h = window_fit_size(width, height)
    if abs((right - left) - w) < 8 and abs((bottom - top) - h) < 8:
        return False
    import ctypes

    # SWP_NOZORDER | SWP_NOACTIVATE — resize without a focus fight that hitchs UEFN.
    ctypes.windll.user32.SetWindowPos(hwnd, 0, int(left), int(top), int(w), int(h), 0x0014)
    return True


def inject_pointer(
    hwnd: int,
    kind: str,
    nx: float,
    ny: float,
    *,
    button: int = 0,
    delta: int = 0,
) -> None:
    if sys.platform != "win32" or hwnd <= 0 or _is_our_hwnd(hwnd):
        return
    box = _window_box(hwnd)
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
        inject_pointer(
            hwnd,
            kind,
            float(event.get("x") or 0),
            float(event.get("y") or 0),
            button=int(event.get("button") or 0),
            delta=int(event.get("delta") or 0),
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


def _send_mouse(flags: int, data: int) -> None:
    ctypes, extra, Mouse, _Key, Input = _input_structs()
    inp = Input()
    inp.type = 0
    inp.union.mi = Mouse(0, 0, int(data) & 0xFFFFFFFF, int(flags), 0, ctypes.pointer(extra))
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
