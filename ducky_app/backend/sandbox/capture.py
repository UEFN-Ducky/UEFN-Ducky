"""The windows a sandbox program shows, and a picture of one (even when covered).

PrintWindow asks the window to draw itself, so the capture is right while another window
sits on top of it; a window that draws nothing that way (some GPU apps) falls back to a
screen grab of its rectangle. Linux: the EWMH client list and a screen grab of the window's
rectangle (mss on X11, the ScreenCast portal on Wayland; X can't draw a covered window).
"""

from __future__ import annotations

import ctypes
import io
import sys
from typing import Any

_WIN = sys.platform == "win32"
_LINUX = sys.platform.startswith("linux")


def _x11_windows_of(wanted: set[int]) -> list[dict[str, Any]]:
    from frontend.native_capture import x11_windows

    return [{"hwnd": w["xid"], "pid": w["pid"], "title": w["title"],
             "rect": {k: w["rect"][k] for k in ("left", "top", "right", "bottom")}}
            for w in x11_windows()
            if w["pid"] in wanted and w["rect"]["width"] >= 40 and w["rect"]["height"] >= 40]


def windows_of(pids: list[int]) -> list[dict[str, Any]]:
    """Visible top-level windows owned by ``pids``: hwnd, pid, title, rect."""
    if _LINUX and pids:
        return _x11_windows_of({int(p) for p in pids})
    if not _WIN or not pids:
        return []
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    wanted = {int(p) for p in pids}
    found: list[dict[str, Any]] = []
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd: Any, _lparam: Any) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) not in wanted:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if rect.right - rect.left < 40 or rect.bottom - rect.top < 40:
            return True
        found.append({
            "hwnd": int(hwnd), "pid": int(pid.value), "title": buf.value[:200],
            "rect": {"left": rect.left, "top": rect.top, "right": rect.right, "bottom": rect.bottom},
        })
        return True

    user32.EnumWindows(proc_type(visit), 0)
    return found


def _print_window(hwnd: int, width: int, height: int) -> Any:
    from ctypes import wintypes

    from PIL import Image

    user32 = ctypes.WinDLL("user32")
    gdi32 = ctypes.WinDLL("gdi32")
    user32.GetWindowDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    hwnd_dc = user32.GetWindowDC(hwnd)
    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
    old = gdi32.SelectObject(mem_dc, bitmap)
    try:
        ok = user32.PrintWindow(hwnd, mem_dc, 2)  # PW_RENDERFULLCONTENT

        class _Header(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]

        header = _Header(ctypes.sizeof(_Header), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        pixels = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetDIBits(mem_dc, bitmap, 0, height, pixels, ctypes.byref(header), 0)
        image = Image.frombuffer("RGBA", (width, height), pixels, "raw", "BGRA", 0, 1).convert("RGB")
        return image if ok else None
    finally:
        gdi32.SelectObject(mem_dc, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, hwnd_dc)


def _blank(image: Any) -> bool:
    extrema = image.convert("L").getextrema()
    return extrema[0] == extrema[1]


def _visible_frame(hwnd: int, rect: dict[str, int]) -> dict[str, int]:
    """The window's visible frame. GetWindowRect also counts the invisible resize borders
    (about 7 px left, right and bottom), which captured whatever was behind the window."""
    from ctypes import wintypes

    frame = wintypes.RECT()
    try:
        failed = ctypes.WinDLL("dwmapi").DwmGetWindowAttribute(
            wintypes.HWND(hwnd), 9, ctypes.byref(frame), ctypes.sizeof(frame))  # DWMWA_EXTENDED_FRAME_BOUNDS
    except (OSError, AttributeError):
        return rect
    if failed or frame.right <= frame.left or frame.bottom <= frame.top:
        return rect
    return {"left": max(frame.left, rect["left"]), "top": max(frame.top, rect["top"]),
            "right": min(frame.right, rect["right"]), "bottom": min(frame.bottom, rect["bottom"])}


def capture(window: dict[str, Any], conv_id: str) -> dict[str, Any]:
    """Save a PNG of ``window`` in the chat's attachments folder (view_image opens it)."""
    rect = window.get("rect") or {}
    width = int(rect.get("right", 0)) - int(rect.get("left", 0))
    height = int(rect.get("bottom", 0)) - int(rect.get("top", 0))
    if width <= 0 or height <= 0:
        return {"ok": False, "error": "That window has no size on screen (minimized?)."}
    full = {k: int(rect[k]) for k in ("left", "top", "right", "bottom")}
    frame = _visible_frame(int(window["hwnd"]), full) if _WIN else full
    crop = (frame["left"] - full["left"], frame["top"] - full["top"],
            frame["right"] - full["left"], frame["bottom"] - full["top"])
    image = None
    try:
        image = _print_window(int(window["hwnd"]), width, height)
    except Exception:
        image = None
    if image is not None and not _blank(image):
        image = image.crop(crop)
    elif not _WIN:
        try:
            from frontend.native_capture import grab_image

            image = grab_image(frame["left"], frame["top"], frame["right"] - frame["left"], frame["bottom"] - frame["top"])
        except Exception as exc:
            return {"ok": False, "error": f"Could not capture the window: {exc}"}
    else:
        try:
            from PIL import ImageGrab

            box = (frame["left"], frame["top"], frame["right"], frame["bottom"])
            image = ImageGrab.grab(bbox=box, all_screens=True)
        except Exception as exc:
            return {"ok": False, "error": f"Could not capture the window: {exc}"}
    width, height = image.size
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    from frontend.ui_web.tool_captures import save_capture_for_agents

    saved = save_capture_for_agents(buf.getvalue(), prefix="sandbox_window", conv_id=conv_id)
    if not saved.get("ok"):
        return {"ok": False, "error": str(saved.get("error") or "The capture was not saved.")}
    return {"ok": True, "hwnd": window["hwnd"], "title": window.get("title", ""), "path": saved.get("path"),
            "media_url": saved.get("media_url"), "width": width, "height": height}
