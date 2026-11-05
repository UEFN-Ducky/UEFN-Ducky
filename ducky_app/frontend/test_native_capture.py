import ctypes
import sys
import time

import pytest

from frontend import native_capture


@pytest.mark.skipif(sys.platform != "win32", reason="GDI")
def test_grab_returns_bgra_of_the_rect_on_any_monitor():
    assert len(native_capture.grab(0, 0, 64, 32)) == 64 * 32 * 4
    # Left of the primary monitor (a Display 2 at negative x, or off-desktop): never raises.
    assert len(native_capture.grab(-200, 0, 64, 32)) == 64 * 32 * 4


def test_even_rect_keeps_sizes_even_and_non_zero():
    assert native_capture.even_rect(-1920, 0, -1303, 1033) == (-1920, 0, 616, 1032)
    assert native_capture.even_rect(5, 5, 5, 5)[2:] == (2, 2)


class _FakeGrabber:
    closed = False

    def grab_into(self, left, top, w, h, dst):
        ctypes.memset(dst, 7, w * h * 4)
        return True

    def close(self):
        _FakeGrabber.closed = True


class _Target:
    """A consumer with one buffer; busy (None) while `busy` is set."""

    def __init__(self):
        self.buf = ctypes.create_string_buffer(0)
        self.busy = False

    def __call__(self, w, h):
        if self.busy:
            return None
        if len(self.buf) < w * h * 4:
            self.buf = ctypes.create_string_buffer(w * h * 4)
        return ctypes.addressof(self.buf)


def test_capturer_paces_frames_follows_the_rect_and_stops_fast():
    frames = []
    t = _Target()
    cap = native_capture.Capturer(lambda: (0, 0, 8, 4), 30, t, lambda w, h: frames.append((w, h)), _FakeGrabber).start()
    time.sleep(0.5)
    t0 = time.perf_counter()
    cap.stop()
    assert time.perf_counter() - t0 < 0.2
    assert not cap.running and _FakeGrabber.closed
    assert 12 <= len(frames) <= 17
    assert frames[0] == (8, 4) and t.buf.raw[:4] == bytes([7]) * 4


def test_capturer_skips_frames_while_the_consumer_is_busy_and_slows_down(monkeypatch):
    monkeypatch.setattr(native_capture, "WINDOW_S", 0.1)
    frames = []
    t = _Target()

    def on_frame(w, h):
        frames.append(1)
        t.busy = True  # the consumer holds the frame for 100 ms

        def release():
            time.sleep(0.1)
            t.busy = False

        import threading

        threading.Thread(target=release, daemon=True).start()

    cap = native_capture.Capturer(lambda: (0, 0, 2, 2), 60, t, on_frame, _FakeGrabber).start()
    time.sleep(0.45)
    cap.stop()
    assert len(frames) <= 6  # ~10/s possible: the rest were skipped, not queued
    assert cap.skipped > 0 and cap.fps < 30


def test_capturer_starts_at_30_and_rises_to_60_only_while_under_budget(monkeypatch):
    monkeypatch.setattr(native_capture, "WINDOW_S", 0.05)
    cap = native_capture.Capturer(lambda: (0, 0, 2, 2), 60, _Target(), lambda w, h: None, _FakeGrabber)
    assert cap.fps == 30
    cap.start()
    time.sleep(0.4)
    assert cap.fps == 60
    # The consumer runs late (a busy UI thread): back down.
    for _ in range(4):
        cap.report_lag(0.5)
        time.sleep(0.07)
    cap.stop()
    assert cap.fps <= 30


def _displays_asleep() -> bool:
    """True only while idle time has reached the power plan's "turn off display after" (0 = never)."""
    try:
        import uuid
        from ctypes import wintypes

        class _LastInput(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        class _PowerStatus(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", ctypes.c_ubyte),
                ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte),
                ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", wintypes.DWORD),
                ("BatteryFullLifeTime", wintypes.DWORD),
            ]

        user32, kernel32, powrprof = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.powrprof
        li = _LastInput(ctypes.sizeof(_LastInput), 0)
        if not user32.GetLastInputInfo(ctypes.byref(li)):
            return False
        idle_s = ((kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000

        ps = _PowerStatus()
        on_battery = bool(kernel32.GetSystemPowerStatus(ctypes.byref(ps))) and ps.ACLineStatus == 0
        read = powrprof.PowerReadDCValueIndex if on_battery else powrprof.PowerReadACValueIndex
        read.argtypes = [ctypes.c_void_p] * 4 + [ctypes.POINTER(wintypes.DWORD)]
        powrprof.PowerGetActiveScheme.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]

        scheme = ctypes.c_void_p()
        if powrprof.PowerGetActiveScheme(None, ctypes.byref(scheme)):
            return False
        try:
            sub = ctypes.create_string_buffer(uuid.UUID("7516b95f-f776-4464-8c53-06167f40cc99").bytes_le, 16)
            idle = ctypes.create_string_buffer(uuid.UUID("3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e").bytes_le, 16)
            after = wintypes.DWORD()
            if read(None, scheme, sub, idle, ctypes.byref(after)):
                return False
        finally:
            kernel32.LocalFree(scheme)
        return after.value > 0 and idle_s >= after.value
    except Exception:
        return False


@pytest.mark.skipif(sys.platform != "win32", reason="DXGI")
def test_dxgi_grabs_every_monitor_fast_enough_for_60fps_and_always_gives_a_first_frame():
    from frontend.native_capture_dxgi import DxgiGrabber

    try:
        g = DxgiGrabber()
    except OSError:
        pytest.skip("no DXGI duplication here (remote session / no GPU)")
    try:
        for o in g._outputs:
            left, top, right, bottom = o.rect
            w, h = min(1920, right - left) & ~1, min(1080, bottom - top) & ~1
            # A new rect on a still screen still gets its first picture.
            first = g.grab(left, top, w, h)
            if first is None and _displays_asleep():
                pytest.skip("the displays are off for idleness: Windows draws no frame to duplicate")
            assert first is not None and len(first) == w * h * 4
            dst = ctypes.create_string_buffer(w * h * 4)
            g.grab_into(left, top, w, h, ctypes.addressof(dst))  # straight into a caller buffer
            t0 = time.perf_counter()
            for _ in range(20):
                g.grab(left, top, w, h)
            per = (time.perf_counter() - t0) / 20
            print(f"DXGI {o.rect} {w}x{h}: {per * 1000:.2f} ms")
            assert per < 1 / 60
    finally:
        g.close()


def test_capturer_sends_nothing_while_the_screen_is_still():
    class _Still(_FakeGrabber):
        def grab_into(self, left, top, w, h, dst):
            return False

    frames = []
    cap = native_capture.Capturer(lambda: (0, 0, 2, 2), 60, _Target(), lambda *a: frames.append(a), _Still).start()
    time.sleep(0.2)
    cap.stop()
    assert frames == []


@pytest.mark.skipif(sys.platform != "win32", reason="GDI")
def test_real_grab_of_a_1080p_rect_is_fast_enough_for_30fps():
    g = native_capture._Grabber()
    try:
        g.grab(0, 0, 1920, 1080)
        t0 = time.perf_counter()
        for _ in range(10):
            g.grab(0, 0, 1920, 1080)
        per = (time.perf_counter() - t0) / 10
    finally:
        g.close()
    print(f"BitBlt 1920x1080: {per * 1000:.1f} ms")
    assert per < 1 / 30


@pytest.mark.skipif(sys.platform != "win32", reason="GDI")
def test_grab_into_fills_the_callers_buffer():
    g = native_capture._Grabber()
    dst = ctypes.create_string_buffer(b"\x55" * (16 * 8 * 4))
    try:
        assert g.grab_into(0, 0, 16, 8, ctypes.addressof(dst)) is True
    finally:
        g.close()
    assert dst.raw != b"\x55" * (16 * 8 * 4)  # overwritten with screen pixels
