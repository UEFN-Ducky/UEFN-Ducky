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


def _wake_displays():
    """Power the displays on for idleness-off monitors; returns the undo. Draws nothing, no window.

    Desktop Duplication only hands over frames Windows composes for a powered output, so a test
    window or private desktop can't stand in: the display itself must be on.
    """
    import ctypes.wintypes as wt

    kernel32, user32 = ctypes.windll.kernel32, ctypes.windll.user32
    kernel32.SetThreadExecutionState.restype = wt.DWORD
    # ES_CONTINUOUS | ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED: on, and kept on while we grab.
    kernel32.SetThreadExecutionState(0x80000003)

    class _MouseInput(ctypes.Structure):
        _fields_ = [
            ("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
            ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t),
        ]

    class _Input(ctypes.Structure):
        _fields_ = [("type", wt.DWORD), ("mi", _MouseInput)]  # MOUSEINPUT is the union's largest member

    # A zero-distance relative move: resets the idle timer (wakes a monitor that ignores the
    # execution state) without moving the pointer.
    inp = _Input(0, _MouseInput(0, 0, 0, 0x0001, 0, 0))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_Input))
    return lambda: kernel32.SetThreadExecutionState(0x80000000)


def _first_frame(grab, wake, timeout_s: float = 6.0):
    """The first picture of a rect; a sleeping display is woken and given time, never skipped."""
    first = grab()
    if first is not None:
        return first
    restore = wake()
    try:
        deadline = time.perf_counter() + timeout_s
        while time.perf_counter() < deadline:
            try:
                first = grab()
            except OSError:
                first = None  # access lost while the output powers up: the grabber reopened it
            if first is not None:
                return first
            time.sleep(0.1)
        return None
    finally:
        restore()


def test_first_frame_wakes_a_sleeping_display_and_retries_instead_of_skipping():
    calls = {"wake": 0, "restore": 0}
    answers = [None, OSError("access lost"), None, b"px"]

    def grab():
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    def wake():
        calls["wake"] += 1
        return lambda: calls.__setitem__("restore", calls["restore"] + 1)

    assert _first_frame(grab, wake) == b"px"
    assert calls == {"wake": 1, "restore": 1}


def test_first_frame_gives_none_so_the_caller_fails_when_no_frame_ever_comes():
    calls = []
    assert _first_frame(lambda: None, lambda: (calls.append("wake"), lambda: calls.append("restore"))[1], 0.3) is None
    assert calls == ["wake", "restore"]


def test_first_frame_leaves_an_awake_display_alone():
    assert _first_frame(lambda: b"px", lambda: pytest.fail("woke an awake display")) == b"px"


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
            # A new rect on a still screen still gets its first picture, displays asleep or not.
            first = _first_frame(lambda: g.grab(left, top, w, h), _wake_displays)
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
