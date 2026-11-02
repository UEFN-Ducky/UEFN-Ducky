import json
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
        def grab(self, left, top, w, h):
            return bytes([7]) * (w * h * 4)

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
    assert m["type"] == "native-frame" and m["slot"] == 1 and m["view"] == "monitor:1"


def test_a_display_view_captures_that_whole_monitor(monkeypatch):
    # Watching all of Display 2 (left of the primary) used to give a 2 px line.
    import frontend.window_view as wv

    monkeypatch.setattr(wv, "_monitors", lambda: [(0, 0, 2560, 1440), (-1920, 0, 1920, 1080)])
    monkeypatch.setattr(wv, "_screen_metrics", lambda: (-1920, 0, 4480, 1440, 2560, 1440))
    monkeypatch.setattr(wv.sys, "platform", "win32")
    seen = {}

    class _Cap:
        def __init__(self, rect_fn, fps, on_frame):
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


def test_start_refuses_without_the_main_webview_or_an_off_screen_view(monkeypatch):
    bridge._host.clear()
    assert bridge.start("123")["ok"] is False
    bridge.register(_FakeCore(), object())
    assert bridge.start("123", rect_fn=lambda: None)["error"] == "view not on screen"
    assert bridge.start("")["ok"] is False
