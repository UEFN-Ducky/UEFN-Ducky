"""A picture of Ducky's window from the last session, shown at the next start.

The live window takes ~2 s to draw (Python, .NET, WebView2, the page). The launcher shows
this picture at the same place in ~0.5 s (frontend/snapshot_splash.py) and closes it once
the page has painted, so Ducky looks ready right away. Saved as an uncompressed 32-bit BMP
(Windows loads it with one call, no decoder) beside a small JSON with where it was.
"""
from __future__ import annotations

import json
import os
import struct
import sys
import time
from pathlib import Path
from typing import Any

BMP_NAME = "boot_snapshot.bmp"
META_NAME = "boot_snapshot.json"
MAX_AGE_S = 30 * 24 * 3600
MIN_SIDE = 200
MAX_SIDE = 8000
MIN_COLOURS = 24  # a blank or single-colour capture (window not drawn yet) is not worth showing


def _dir() -> Path:
    from frontend.settings import default_app_data_dir

    return default_app_data_dir()


def paths(folder: Path | None = None) -> tuple[Path, Path]:
    folder = folder or _dir()
    return folder / BMP_NAME, folder / META_NAME


def bmp_bytes(width: int, height: int, bgra_top_down: bytes) -> bytes:
    """A bottom-up 32 bpp BI_RGB bitmap file from top-down BGRA rows."""
    row = width * 4
    if width <= 0 or height <= 0 or len(bgra_top_down) != row * height:
        raise ValueError("pixel buffer does not match the size")
    pixels = b"".join(bgra_top_down[y * row:(y + 1) * row] for y in range(height - 1, -1, -1))
    header = struct.pack("<2sIHHI", b"BM", 14 + 40 + len(pixels), 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 32, 0, len(pixels), 2835, 2835, 0, 0)
    return header + info + pixels


def bmp_size(path: Path) -> tuple[int, int] | None:
    """``(width, height)`` of a 32 bpp BMP written by :func:`bmp_bytes`, or None."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(54)
    except OSError:
        return None
    if len(head) < 54 or head[:2] != b"BM":
        return None
    width, height = struct.unpack_from("<ii", head, 18)
    bits = struct.unpack_from("<H", head, 28)[0]
    if bits != 32 or width <= 0 or height == 0:
        return None
    return width, abs(height)


def distinct_colours(bgra: bytes, samples: int = 4000) -> int:
    """Rough count of distinct colours (4 bits per channel) over evenly spaced pixels."""
    count = len(bgra) // 4
    if not count:
        return 0
    step = max(1, count // samples)
    seen = set()
    for i in range(0, count, step):
        o = i * 4
        seen.add((bgra[o] >> 4, bgra[o + 1] >> 4, bgra[o + 2] >> 4))
    return len(seen)


def _sane(width: int, height: int) -> bool:
    return MIN_SIDE <= width <= MAX_SIDE and MIN_SIDE <= height <= MAX_SIDE


def save(rect: tuple[int, int, int, int], bgra_top_down: bytes, *, folder: Path | None = None,
         version: str = "") -> bool:
    """Store the picture of a window at ``rect`` = (x, y, width, height). False when not kept."""
    x, y, width, height = (int(v) for v in rect)
    if not _sane(width, height) or len(bgra_top_down) != width * height * 4:
        return False
    if distinct_colours(bgra_top_down) < MIN_COLOURS:
        return False
    bmp, meta = paths(folder)
    try:
        bmp.parent.mkdir(parents=True, exist_ok=True)
        part = bmp.with_name(f"{bmp.name}.{os.getpid()}.part")
        part.write_bytes(bmp_bytes(width, height, bgra_top_down))
        os.replace(part, bmp)
        record = {"x": x, "y": y, "w": width, "h": height, "at": time.time(), "version": version}
        meta_part = meta.with_name(f"{meta.name}.{os.getpid()}.part")
        meta_part.write_text(json.dumps(record), encoding="utf-8")
        os.replace(meta_part, meta)
    except OSError:
        return False
    return True


def load(*, folder: Path | None = None, now: float | None = None) -> dict[str, Any] | None:
    """``{path, x, y, w, h}`` of a usable picture, or None (missing, old, broken, odd size)."""
    bmp, meta = paths(folder)
    try:
        record = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    try:
        x, y, w, h = (int(record[k]) for k in ("x", "y", "w", "h"))
        at = float(record.get("at") or 0.0)
    except (KeyError, TypeError, ValueError):
        return None
    if not _sane(w, h) or (now if now is not None else time.time()) - at > MAX_AGE_S:
        return None
    if bmp_size(bmp) != (w, h):
        return None
    return {"path": str(bmp), "x": x, "y": y, "w": w, "h": h}


def forget(*, folder: Path | None = None) -> None:
    for path in paths(folder):
        try:
            path.unlink()
        except OSError:
            pass


def capture_window(hwnd: int, *, folder: Path | None = None) -> bool:
    """PrintWindow (full content, works for WebView2) the visible window and save it.

    Physical pixels: the calling thread is made per-monitor DPI aware for the call only,
    the process' own DPI mode is never changed.
    """
    if sys.platform != "win32" or not hwnd:
        return False
    import ctypes
    from ctypes import wintypes as wt

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.GetWindowDC.restype = wt.HDC
    user32.GetWindowDC.argtypes = [wt.HWND]
    user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
    user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
    user32.IsWindowVisible.argtypes = [wt.HWND]
    user32.IsIconic.argtypes = [wt.HWND]
    user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    gdi32.CreateCompatibleDC.restype = wt.HDC
    gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
    gdi32.CreateDIBSection.restype = wt.HBITMAP
    gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT, ctypes.POINTER(ctypes.c_void_p),
                                       wt.HANDLE, wt.DWORD]
    gdi32.SelectObject.restype = wt.HGDIOBJ
    gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
    gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wt.HDC]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                    ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

    hwnd = wt.HWND(hwnd)
    old_ctx = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    try:
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return False
        r = wt.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return False
        width, height = r.right - r.left, r.bottom - r.top
        if not _sane(width, height):
            return False
        window_dc = user32.GetWindowDC(hwnd)
        mem_dc = gdi32.CreateCompatibleDC(window_dc)
        info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        dib = gdi32.CreateDIBSection(window_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        old = gdi32.SelectObject(mem_dc, dib) if dib else None
        try:
            if not dib or not bits.value or not user32.PrintWindow(hwnd, mem_dc, 2):  # PW_RENDERFULLCONTENT
                return False
            pixels = ctypes.string_at(bits.value, width * height * 4)
        finally:
            if old:
                gdi32.SelectObject(mem_dc, old)
            if dib:
                gdi32.DeleteObject(dib)
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(hwnd, window_dc)
    except Exception:
        return False
    finally:
        if old_ctx:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(old_ctx))
    try:
        from frontend import __version__ as version
    except Exception:
        version = ""
    return save((r.left, r.top, width, height), pixels, folder=folder, version=str(version))
