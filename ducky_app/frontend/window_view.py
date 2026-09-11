"""Look at other desktop windows through the remote panel tunnel.

ponytail: grab the window's on-screen rectangle (Pillow ImageGrab). Covered
windows show whatever is on top; minimized frames are skipped. Windows
Graphics Capture if they need occluded GPU windows.
"""

from __future__ import annotations

import io
import os
import sys
from typing import Any

_MAX_EDGE = 1600
_KIND_ORDER = {"uefn": 0, "blender": 1, "app": 2}


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
    img.save(buf, format="JPEG", quality=65, optimize=True)
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


def _enum_windows() -> list[dict[str, Any]]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    GW_OWNER = 4
    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080
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
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindow(hwnd, GW_OWNER):
            return True
        ex = int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE) or 0)
        if ex & WS_EX_TOOLWINDOW:
            return True
        if _cloaked(hwnd):
            return True
        title = _text(hwnd)
        if not title:
            return True
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) == me:
            return True
        box = _window_box(int(hwnd))
        if not box:
            return True
        left, top, right, bottom = box
        if (right - left) < 80 or (bottom - top) < 80:
            return True
        exe = _exe_name(int(pid.value))
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


def _cloaked(hwnd: int) -> bool:
    try:
        import ctypes

        cloak = ctypes.c_int(0)
        ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloak), 4)
        return int(cloak.value) != 0
    except Exception:
        return False


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
