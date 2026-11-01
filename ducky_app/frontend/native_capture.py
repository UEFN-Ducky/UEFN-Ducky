"""Prompt-free capture of any desktop rect, on any monitor (the phone stream's source).

WebView2's whole-desktop getUserMedia always raises Chromium's "Screen Sharing Request" box
and getDisplayMedia gives one monitor, so this process grabs pixels itself, the way Chrome
Remote Desktop's native host does. ponytail: GDI BitBlt (ctypes, no dependency); DXGI Desktop
Duplication if a 4K/60 grab ever costs too much.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Callable

_SRCCOPY = 0x00CC0020
_CAPTUREBLT = 0x40000000


class _Grabber:
    """One screen DC + a DIB section kept between grabs of the same size."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ct = ctypes
        self._u = ctypes.windll.user32
        self._g = ctypes.windll.gdi32
        self._g.CreateDIBSection.restype = wintypes.HBITMAP
        self._g.CreateDIBSection.argtypes = [
            wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
        ]
        self._g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        self._g.SelectObject.restype = wintypes.HGDIOBJ
        self._g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        self._g.DeleteDC.argtypes = [wintypes.HDC]
        self._g.CreateCompatibleDC.argtypes = [wintypes.HDC]
        self._g.CreateCompatibleDC.restype = wintypes.HDC
        self._g.BitBlt.argtypes = [wintypes.HDC] + [ctypes.c_int] * 4 + [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
        self._u.GetDC.restype = wintypes.HDC
        self._u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        self._screen = self._u.GetDC(None)
        self._mem = self._g.CreateCompatibleDC(self._screen)
        self._bmp = None
        self._old = None
        self._bits = ctypes.c_void_p()
        self._size = (0, 0)

    def _ensure(self, w: int, h: int) -> None:
        if self._size == (w, h):
            return
        ct = self._ct
        if self._bmp:
            self._g.SelectObject(self._mem, self._old)
            self._g.DeleteObject(self._bmp)
        # BITMAPINFOHEADER: 32 bpp, negative height = top-down rows.
        hdr = (ct.c_int32 * 10)()
        ct.memmove(hdr, (ct.c_uint32 * 1)(40), 4)
        hdr[1], hdr[2] = w, -h
        ct.memmove(ct.byref(hdr, 12), (ct.c_uint16 * 2)(1, 32), 4)
        self._bmp = self._g.CreateDIBSection(self._mem, ct.byref(hdr), 0, ct.byref(self._bits), None, 0)
        if not self._bmp:
            raise OSError("CreateDIBSection failed")
        self._old = self._g.SelectObject(self._mem, self._bmp)
        self._size = (w, h)

    def grab(self, left: int, top: int, w: int, h: int) -> bytes:
        self._ensure(w, h)
        if not self._g.BitBlt(self._mem, 0, 0, w, h, self._screen, left, top, _SRCCOPY | _CAPTUREBLT):
            raise OSError("BitBlt failed")
        return self._ct.string_at(self._bits, w * h * 4)

    def close(self) -> None:
        if self._bmp:
            self._g.SelectObject(self._mem, self._old)
            self._g.DeleteObject(self._bmp)
            self._bmp = None
        self._g.DeleteDC(self._mem)
        self._u.ReleaseDC(None, self._screen)


def even_rect(left: int, top: int, right: int, bottom: int) -> tuple[int, int, int, int]:
    """(left, top, w, h) with even, non-zero w/h: H.264 needs even sizes."""
    w = max(2, (right - left) & ~1)
    h = max(2, (bottom - top) & ~1)
    return left, top, w, h


def grab(left: int, top: int, w: int, h: int) -> bytes:
    """BGRA, top-down, w*h*4 bytes of the virtual desktop at (left, top)."""
    if sys.platform != "win32":
        raise OSError("native capture is Windows only")
    g = _Grabber()
    try:
        return g.grab(left, top, w, h)
    finally:
        g.close()


RectFn = Callable[[], "tuple[int, int, int, int] | None"]
FrameFn = Callable[[bytes, int, int], None]


class Capturer:
    """Grabs rect_fn()'s (left, top, right, bottom) at fps and hands each frame to on_frame.

    Frames that come due while on_frame still runs are dropped, never queued (latency first).
    """

    def __init__(self, rect_fn: RectFn, fps: int, on_frame: FrameFn, grabber_factory=None) -> None:
        self._rect_fn = rect_fn
        self._period = 1.0 / max(1, min(60, int(fps)))
        self._on_frame = on_frame
        self._factory = grabber_factory or _Grabber
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="native-capture", daemon=True)

    def start(self) -> "Capturer":
        self._thread.start()
        return self

    def stop(self, timeout: float = 1.0) -> None:
        self._stop.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout)

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    def _run(self) -> None:
        g = self._factory()
        try:
            due = time.perf_counter()
            while not self._stop.is_set():
                rect = self._rect_fn()
                if rect:
                    left, top, w, h = even_rect(*rect)
                    try:
                        self._on_frame(g.grab(left, top, w, h), w, h)
                    except Exception:
                        pass  # ponytail: a failed grab (secure desktop, mid-resize) skips one frame
                due += self._period
                now = time.perf_counter()
                if due < now:
                    due = now  # behind: drop the missed frames
                self._stop.wait(due - now)
        finally:
            g.close()
