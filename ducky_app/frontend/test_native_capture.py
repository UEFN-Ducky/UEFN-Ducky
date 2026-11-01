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

    def grab(self, left, top, w, h):
        return b"\0" * (w * h * 4)

    def close(self):
        _FakeGrabber.closed = True


def test_capturer_paces_frames_follows_the_rect_and_stops_fast():
    frames = []
    cap = native_capture.Capturer(
        lambda: (0, 0, 8, 4), 30, lambda buf, w, h: frames.append((len(buf), w, h)), _FakeGrabber
    ).start()
    time.sleep(0.5)
    t0 = time.perf_counter()
    cap.stop()
    assert time.perf_counter() - t0 < 0.2
    assert not cap.running and _FakeGrabber.closed
    assert 12 <= len(frames) <= 17
    assert frames[0] == (8 * 4 * 4, 8, 4)


def test_capturer_drops_frames_while_the_consumer_is_slow():
    frames = []

    def slow(buf, w, h):
        frames.append(1)
        time.sleep(0.1)

    cap = native_capture.Capturer(lambda: (0, 0, 2, 2), 60, slow, _FakeGrabber).start()
    time.sleep(0.45)
    cap.stop()
    assert len(frames) <= 6  # 60 fps asked, ~10/s possible: the rest were dropped, not queued


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
            assert first is not None and len(first) == w * h * 4
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
        def grab(self, left, top, w, h):
            return None

    frames = []
    cap = native_capture.Capturer(lambda: (0, 0, 2, 2), 60, lambda *a: frames.append(a), _Still).start()
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
