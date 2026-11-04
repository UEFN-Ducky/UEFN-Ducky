"""Prompt-free capture of any desktop rect, on any monitor (the phone stream's source).

WebView2's whole-desktop getUserMedia always raises Chromium's "Screen Sharing Request" box
and getDisplayMedia gives one monitor, so this process grabs pixels itself, the way Chrome
Remote Desktop's native host does: DXGI Desktop Duplication first (native_capture_dxgi, ~3 ms
for 1080p, up to the refresh rate), GDI BitBlt when that can't start or a rect spans two
monitors (BitBlt waits on the compositor: ~20 ms for 1080p, under 50 fps).
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

    def _blit(self, left: int, top: int, w: int, h: int) -> None:
        self._ensure(w, h)
        if not self._g.BitBlt(self._mem, 0, 0, w, h, self._screen, left, top, _SRCCOPY | _CAPTUREBLT):
            raise OSError("BitBlt failed")

    def grab(self, left: int, top: int, w: int, h: int) -> bytes:
        self._blit(left, top, w, h)
        return self._ct.string_at(self._bits, w * h * 4)

    def grab_into(self, left: int, top: int, w: int, h: int, dst: int) -> bool:
        """The rect at address dst. memmove drops the GIL; string_at would hold it for the copy."""
        self._blit(left, top, w, h)
        self._ct.memmove(dst, self._bits, w * h * 4)
        return True

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


class _BestGrabber:
    """DXGI when it works, BitBlt for what it can't do (start, rect across monitors, lost access)."""

    def __init__(self) -> None:
        self._gdi = _Grabber()
        try:
            from frontend.native_capture_dxgi import DxgiGrabber

            self._dxgi = DxgiGrabber()
        except Exception:
            self._dxgi = None

    def grab_into(self, left: int, top: int, w: int, h: int, dst: int) -> bool:
        if self._dxgi is not None:
            try:
                return self._dxgi.grab_into(left, top, w, h, dst)
            except OSError:
                pass
        return self._gdi.grab_into(left, top, w, h, dst)

    def close(self) -> None:
        if self._dxgi is not None:
            self._dxgi.close()
        self._gdi.close()


RectFn = Callable[[], "tuple[int, int, int, int] | None"]
# Where the next w x h frame goes: an address with room for w*h*4 bytes, or None when the
# consumer still has the last frame (then this one is skipped, never queued).
TargetFn = Callable[[int, int], "int | None"]
FrameFn = Callable[[int, int], None]  # the frame target_fn gave is filled: send it

START_FPS = 30
MIN_FPS = 10
WINDOW_S = 1.0  # fps adapts once per window


class Capturer:
    """Grabs rect_fn()'s (left, top, right, bottom) into target_fn's buffer and calls on_frame.

    Latency first: a frame due while the consumer is busy is dropped, never queued. The rate
    starts at min(START_FPS, fps) and adapts once a second: grabs that eat over half the period
    or a consumer that skips over a quarter of the frames lower it by a third (not below
    MIN_FPS); a clean second raises it by 10 up to fps. report_lag(s) tells it how late the
    consumer ran (e.g. the UI thread's queue).
    """

    def __init__(self, rect_fn: RectFn, fps: int, target_fn: TargetFn, on_frame: FrameFn, grabber_factory=None) -> None:
        self._rect_fn = rect_fn
        self.max_fps = max(1, min(60, int(fps)))
        self.fps = min(START_FPS, self.max_fps)
        self._target_fn = target_fn
        self._on_frame = on_frame
        self._factory = grabber_factory or _BestGrabber
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="native-capture", daemon=True)
        self._lag = 0.0
        self._win = [0, 0, time.perf_counter()]  # frames due, over budget, window start
        self.sent = 0
        self.skipped = 0

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

    def report_lag(self, seconds: float) -> None:
        """The consumer ran this long after on_frame handed it a frame."""
        self._lag = max(self._lag, seconds)

    def _adapt(self, over: bool, now: float) -> None:
        win = self._win
        win[0] += 1
        win[1] += 1 if over else 0
        if now - win[2] < WINDOW_S:
            return
        due, bad = win[0], win[1]
        if self._lag > 2.0 / self.fps or bad * 4 > due:
            self.fps = max(MIN_FPS, self.fps * 2 // 3)
        elif bad == 0 and self.fps < self.max_fps:
            self.fps = min(self.max_fps, self.fps + 10)
        self._win = [0, 0, now]
        self._lag = 0.0

    def _tick(self, g) -> bool:
        """One due frame; True when it went over budget (slow grab or consumer still busy)."""
        rect = self._rect_fn()
        if not rect:
            return False
        left, top, w, h = even_rect(*rect)
        dst = self._target_fn(w, h)
        if dst is None:
            self.skipped += 1
            return True
        t0 = time.perf_counter()
        if g.grab_into(left, top, w, h, dst):  # False: nothing changed on screen, nothing to send
            self._on_frame(w, h)
            self.sent += 1
        return time.perf_counter() - t0 > 0.5 / self.fps

    def _run(self) -> None:
        g = self._factory()
        try:
            due = time.perf_counter()
            while not self._stop.is_set():
                try:
                    over = self._tick(g)
                except Exception:
                    over = False  # ponytail: a failed grab (secure desktop, mid-resize) skips one frame
                now = time.perf_counter()
                self._adapt(over, now)
                due += 1.0 / self.fps
                if due < now:
                    due = now  # behind: drop the missed frames
                self._stop.wait(due - now)
        finally:
            g.close()
