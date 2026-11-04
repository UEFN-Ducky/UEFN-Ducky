import ctypes
import json
import queue
import threading
import time

from frontend.ui_web import native_capture_bridge as bridge


class _FakeBuf:
    def __init__(self, size):
        import ctypes

        self.mem = ctypes.create_string_buffer(size)
        self.closed = False

        class _Ptr:
            def __init__(self, addr):
                self.addr = addr

            def ToInt64(self):
                return self.addr

        self.Buffer = _Ptr(ctypes.addressof(self.mem))

    def Close(self):
        self.closed = True


class _FakeCore:
    def __init__(self):
        self.posted = []
        self.messages = []

        class _Env:
            def CreateSharedBuffer(self, size):
                return _FakeBuf(size)

        self.Environment = _Env()

    def PostSharedBufferToScript(self, buf, access, meta):
        self.posted.append((buf, json.loads(meta)))

    def PostWebMessageAsJson(self, msg):
        self.messages.append(json.loads(msg))


def test_start_streams_frames_into_slots_and_stop_closes(monkeypatch):
    core = _FakeCore()
    bridge.register(core, object())
    monkeypatch.setattr(bridge, "_on_ui", lambda fn, wait: fn())

    class _G:
        def grab_into(self, left, top, w, h, dst):
            ctypes.memset(dst, 7, w * h * 4)
            return True

        def close(self):
            pass

    monkeypatch.setattr("frontend.native_capture._BestGrabber", _G)
    out = bridge.start("monitor:1", 30, rect_fn=lambda: (-1920, 0, -1916, 2))
    assert out == {"ok": True, "view": "monitor:1"}
    time.sleep(0.2)
    assert bridge.running() == ["monitor:1"]
    assert bridge.stop("monitor:1") is True
    assert bridge.running() == []
    buf, meta = core.posted[0]
    assert meta == {"view": "monitor:1", "w": 4, "h": 2, "slots": 2}
    assert len(core.posted) == 1  # same size: posted once
    assert buf.mem.raw[:4] == bytes([7]) * 4 and buf.closed
    m = core.messages[1]
    assert m["type"] == "native-frame" and m["slot"] == 1 and m["view"] == "monitor:1" and m["fps"] == 30


def test_a_display_view_captures_that_whole_monitor(monkeypatch):
    # Watching all of Display 2 (left of the primary) used to give a 2 px line.
    import frontend.window_view as wv

    monkeypatch.setattr(wv, "_monitors", lambda: [(0, 0, 2560, 1440), (-1920, 0, 1920, 1080)])
    monkeypatch.setattr(wv, "_screen_metrics", lambda: (-1920, 0, 4480, 1440, 2560, 1440))
    monkeypatch.setattr(wv.sys, "platform", "win32")
    seen = {}

    class _Cap:
        def __init__(self, rect_fn, fps, target_fn, on_frame):
            seen["rect"] = rect_fn()
            self.running = True

        def start(self):
            return self

        def stop(self):
            pass

    monkeypatch.setattr(bridge, "Capturer", _Cap)
    bridge.register(_FakeCore(), object())
    assert bridge.start("monitor:1")["ok"] is True
    assert seen["rect"] == (-1920, 0, 0, 1080)
    bridge.stop("monitor:1")


class _FakeUi:
    """A UI thread that runs BeginInvoke'd callbacks in order; `block` stalls it."""

    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.block = threading.Event()
        self.block.set()
        self.costs: list[float] = []
        self.max_queued = 0
        self._stop = False
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def _run(self):
        while not self._stop:
            self.block.wait()
            try:
                fn, done, out = self.q.get(timeout=0.05)
            except queue.Empty:
                continue
            t0 = time.perf_counter()
            try:
                out.append(fn())
            except Exception as exc:
                out.append(exc)
            self.costs.append(time.perf_counter() - t0)
            done.set()

    def on_ui(self, fn, wait, timeout=5.0):
        done, out = threading.Event(), []
        self.q.put((fn, done, out))
        self.max_queued = max(self.max_queued, self.q.qsize())
        if not wait:
            return None
        if not done.wait(timeout):
            raise TimeoutError("WebView2 UI thread busy")
        if isinstance(out[0], Exception):
            raise out[0]
        return out[0]

    def close(self):
        self._stop = True
        self.block.set()


class _BigGrabber:
    """A 3840x1080 desktop that changes every frame, as fast as memset goes."""

    def grab_into(self, left, top, w, h, dst):
        ctypes.memset(dst, 3, w * h * 4)
        return True

    def close(self):
        pass


def _stream(monkeypatch, ui, secs, during=None):
    core = _FakeCore()
    bridge.register(core, object())
    monkeypatch.setattr(bridge, "_on_ui", ui.on_ui)
    monkeypatch.setattr("frontend.native_capture._BestGrabber", _BigGrabber)
    monkeypatch.setattr("frontend.native_capture.WINDOW_S", 0.2)
    assert bridge.start("desktop", 60, rect_fn=lambda: (0, 0, 3840, 1080))["ok"]
    if during:
        during()
    time.sleep(secs)
    v = bridge._views["desktop"]
    t0 = time.perf_counter()
    assert bridge.stop("desktop") is True
    return core, v, time.perf_counter() - t0


def test_a_60fps_stream_keeps_each_ui_thread_turn_under_2ms(monkeypatch):
    ui = _FakeUi()
    try:
        core, v, _ = _stream(monkeypatch, ui, 1.5)
    finally:
        ui.close()
    frames = [m for m in core.messages if m["type"] == "native-frame"]
    assert len(frames) > 40 and v.cap.fps == 60
    costs = sorted(ui.costs)
    assert costs[int(len(costs) * 0.95)] < 0.002  # the UI thread only posts a tiny message
    assert ui.max_queued <= 2  # one frame post (+ at most a buffer close) ever waits


def test_a_ui_thread_blocked_for_1s_drops_frames_and_never_deadlocks(monkeypatch):
    ui = _FakeUi()

    def stall():
        time.sleep(0.3)
        ui.block.clear()
        threading.Timer(1.0, ui.block.set).start()

    try:
        core, v, stop_s = _stream(monkeypatch, ui, 2.0, during=stall)
    finally:
        ui.close()
    assert ui.max_queued <= 2  # skipped while stalled, never queued up
    assert v.cap.skipped > 0
    assert v.cap.sent > 30  # it went on streaming after the stall
    assert stop_s < 1.2
    seqs = [m["seq"] for m in core.messages if m["type"] == "native-frame"]
    assert seqs == sorted(seqs)


def test_stop_while_the_capture_thread_is_still_inside_a_grab_keeps_the_buffer(monkeypatch):
    core = _FakeCore()
    bridge.register(core, object())
    monkeypatch.setattr(bridge, "_on_ui", lambda fn, wait: fn())
    entered, release = threading.Event(), threading.Event()

    class _Slow:
        def grab_into(self, left, top, w, h, dst):
            entered.set()
            release.wait(3)
            ctypes.memset(dst, 1, w * h * 4)  # would write freed memory if stop closed the buffer
            return True

        def close(self):
            pass

    monkeypatch.setattr("frontend.native_capture._BestGrabber", _Slow)
    bridge.start("monitor:2", 30, rect_fn=lambda: (0, 0, 4, 2))
    assert entered.wait(2)
    v = bridge._views["monitor:2"]
    real_stop = v.cap.stop
    monkeypatch.setattr(v.cap, "stop", lambda timeout=1.0: None)  # as if join timed out
    bridge.stop("monitor:2")
    assert v.buf is not None and not v.buf.closed
    release.set()
    real_stop()
    assert not v.cap.running


def test_start_refuses_without_the_main_webview_or_an_off_screen_view(monkeypatch):
    bridge._host.clear()
    assert bridge.start("123")["ok"] is False
    bridge.register(_FakeCore(), object())
    assert bridge.start("123", rect_fn=lambda: None)["error"] == "view not on screen"
    assert bridge.start("")["ok"] is False
