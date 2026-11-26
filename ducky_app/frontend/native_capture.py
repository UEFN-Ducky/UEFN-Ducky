"""Prompt-free capture of any desktop rect, on any monitor (the phone stream's source).

WebView2's whole-desktop getUserMedia always raises Chromium's "Screen Sharing Request" box
and getDisplayMedia gives one monitor, so this process grabs pixels itself, the way Chrome
Remote Desktop's native host does: DXGI Desktop Duplication first (native_capture_dxgi, ~3 ms
for 1080p, up to the refresh rate), GDI BitBlt when that can't start or a rect spans two
monitors (BitBlt waits on the compositor: ~20 ms for 1080p, under 50 fps).

Linux: X11 grabs with ``mss`` (window bounds from ``python-xlib``'s EWMH client list);
Wayland has no screen reading for clients, so it goes through xdg-desktop-portal ScreenCast
and a PipeWire stream, with a ``persist_mode=2`` restore token so the person consents once.

macOS: ScreenCaptureKit stills (macOS 15.2+), else ``mss`` (CoreGraphics); both need the
Screen Recording permission, which :func:`offer_screen_recording_settings` points to.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

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
    if sys.platform.startswith("linux") or sys.platform == "darwin":
        if sys.platform != "darwin" and is_wayland():
            return shared_portal().grab(left, top, w, h)
        g = MacGrabber() if sys.platform == "darwin" else MssGrabber()
        try:
            return g.grab(left, top, w, h)
        finally:
            g.close()
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


# --- Linux -------------------------------------------------------------------------------


def is_wayland() -> bool:
    """A Wayland session (XWayland's DISPLAY can't read native Wayland windows)."""
    return bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


def grab_image(left: int, top: int, w: int, h: int) -> Any:
    """grab() as an RGB PIL image."""
    from PIL import Image

    return Image.frombuffer("RGBA", (w, h), grab(left, top, w, h), "raw", "BGRA", 0, 1).convert("RGB")


def _memmove_into(dst: int, data: bytes | bytearray) -> None:
    buf = data if isinstance(data, bytearray) else bytearray(data)
    ctypes.memmove(dst, (ctypes.c_char * len(buf)).from_buffer(buf), len(buf))


class MssGrabber:
    """X11: one ``mss`` handle kept between grabs (XGetImage / XShm under the hood)."""

    def __init__(self) -> None:
        import mss

        self._s = mss.mss()

    def grab(self, left: int, top: int, w: int, h: int) -> bytes:
        return bytes(self._s.grab({"left": left, "top": top, "width": w, "height": h}).raw)

    def grab_into(self, left: int, top: int, w: int, h: int, dst: int) -> bool:
        raw = self._s.grab({"left": left, "top": top, "width": w, "height": h}).raw
        _memmove_into(dst, raw)
        return True

    def close(self) -> None:
        self._s.close()


def crop_bgra(data: bytes, fw: int, fh: int, x: int, y: int, w: int, h: int) -> bytes:
    """The w x h rect at (x, y) of a fw x fh BGRA frame; parts outside the frame are black."""
    out = bytearray(w * h * 4)
    x0, x1 = max(0, x), min(fw, x + w)
    if x1 <= x0:
        return bytes(out)
    for row in range(max(0, y), min(fh, y + h)):
        src = (row * fw + x0) * 4
        dst = ((row - y) * w + (x0 - x)) * 4
        out[dst:dst + (x1 - x0) * 4] = data[src:src + (x1 - x0) * 4]
    return bytes(out)


def restore_token_path() -> Path:
    from frontend.app_paths import APP_DATA_DIR_NAME, config_base

    return config_base() / APP_DATA_DIR_NAME / "screencast_restore_token"


_PORTAL = "org.freedesktop.portal.Desktop"
_PORTAL_PATH = "/org/freedesktop/portal/desktop"
_SCREENCAST = "org.freedesktop.portal.ScreenCast"


class PortalBus:
    """The ScreenCast portal over the session bus (jeepney, pure Python). ``request`` calls a
    method that answers through a Request object's Response signal and returns its results."""

    _SIGS = {"CreateSession": "a{sv}", "SelectSources": "oa{sv}", "Start": "osa{sv}"}

    def __init__(self) -> None:
        from jeepney.io.blocking import open_dbus_connection

        self._conn = open_dbus_connection(bus="SESSION", enable_fds=True)
        self._sender = self._conn.unique_name[1:].replace(".", "_")

    @staticmethod
    def _variants(opts: dict[str, Any]) -> dict[str, tuple[str, Any]]:
        out = {}
        for k, v in opts.items():
            sig = "b" if isinstance(v, bool) else "u" if isinstance(v, int) else "s"
            out[k] = (sig, v)
        return out

    def _addr(self) -> Any:
        from jeepney import DBusAddress

        return DBusAddress(_PORTAL_PATH, bus_name=_PORTAL, interface=_SCREENCAST)

    def request(self, method: str, *args: Any, timeout: float = 300.0) -> dict[str, Any]:
        from jeepney import MatchRule, message_bus, new_method_call
        from jeepney.io.blocking import Proxy

        token = "ducky" + uuid.uuid4().hex[:12]
        opts = {**args[-1], "handle_token": token}
        path = f"{_PORTAL_PATH}/request/{self._sender}/{token}"
        rule = MatchRule(type="signal", interface="org.freedesktop.portal.Request", member="Response", path=path)
        Proxy(message_bus, self._conn).AddMatch(rule)
        with self._conn.filter(rule) as queue:
            reply = self._conn.send_and_get_reply(
                new_method_call(self._addr(), method, self._SIGS[method], (*args[:-1], self._variants(opts))))
            if reply.header.message_type.name == "error":
                raise OSError(f"ScreenCast.{method} failed: {reply.body}")
            signal = self._conn.recv_until_filtered(queue, timeout=timeout)
        code, results = signal.body
        if code != 0:
            raise PermissionError(f"ScreenCast.{method} was {'cancelled' if code == 1 else 'refused'} (code {code})")
        return {k: v[1] for k, v in results.items()}

    def open_pipewire_remote(self, session: str) -> int:
        from jeepney import new_method_call

        reply = self._conn.send_and_get_reply(new_method_call(self._addr(), "OpenPipeWireRemote", "oa{sv}", (session, {})))
        fd = reply.body[0]
        return fd.to_raw_fd() if hasattr(fd, "to_raw_fd") else int(fd)


class GstStream:
    """Frames of one PipeWire node as BGRx through GStreamer's pipewiresrc."""

    def __init__(self, fd: int, node: int) -> None:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        self._gst = Gst
        self._pipe = Gst.parse_launch(
            f"pipewiresrc fd={int(fd)} path={int(node)} always-copy=true ! videoconvert ! "
            "video/x-raw,format=BGRx ! appsink name=sink max-buffers=1 drop=true sync=false")
        self._sink = self._pipe.get_by_name("sink")
        self._pipe.set_state(Gst.State.PLAYING)

    def frame(self) -> tuple[int, int, bytes]:
        sample = self._sink.emit("try-pull-sample", 2 * self._gst.SECOND)
        if sample is None:
            raise OSError("no PipeWire frame in 2 s")
        caps = sample.get_caps().get_structure(0)
        buf = sample.get_buffer()
        ok, info = buf.map(self._gst.MapFlags.READ)
        if not ok:
            raise OSError("PipeWire frame could not be read")
        try:
            return caps.get_value("width"), caps.get_value("height"), bytes(info.data)
        finally:
            buf.unmap(info)

    def close(self) -> None:
        self._pipe.set_state(self._gst.State.NULL)


class PortalGrabber:
    """Wayland: one ScreenCast session of a monitor. The first start asks the person; the
    restore token it returns (persist_mode=2: until revoked) makes every later start silent.
    Each start hands back a new single-use token, so it is saved every time."""

    def __init__(self, bus: Any = None, stream_factory: Callable[[int, int], Any] | None = None,
                 token_path: Path | None = None) -> None:
        self._bus = bus if bus is not None else PortalBus()
        self._token_path = token_path or restore_token_path()
        self._lock = threading.Lock()
        session = self._bus.request("CreateSession", {"session_handle_token": "ducky" + uuid.uuid4().hex[:12]})["session_handle"]
        opts: dict[str, Any] = {"types": 1, "multiple": False, "cursor_mode": 2, "persist_mode": 2}  # monitor, cursor drawn in
        token = self._read_token()
        if token:
            opts["restore_token"] = token
        self._bus.request("SelectSources", session, opts)
        started = self._bus.request("Start", session, "", {})
        if started.get("restore_token"):
            self._write_token(str(started["restore_token"]))
        streams = started.get("streams") or []
        if not streams:
            raise OSError("the ScreenCast portal started no stream")
        node, props = streams[0]
        props = {k: (v[1] if isinstance(v, tuple) else v) for k, v in dict(props or {}).items()}
        self.origin = tuple(props.get("position") or (0, 0))
        self.session = session
        self._stream = (stream_factory or GstStream)(self._bus.open_pipewire_remote(session), int(node))

    def _read_token(self) -> str:
        try:
            return self._token_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _write_token(self, token: str) -> None:
        try:
            self._token_path.parent.mkdir(parents=True, exist_ok=True)
            self._token_path.write_text(token, encoding="utf-8")
            os.chmod(self._token_path, 0o600)
        except OSError:
            pass  # ponytail: no token saved = the portal asks again next start, nothing lost

    def grab(self, left: int, top: int, w: int, h: int) -> bytes:
        # ponytail: one monitor at scale 1; multi-monitor / fractional scale needs a stream per output
        with self._lock:
            fw, fh, data = self._stream.frame()
        return crop_bgra(data, fw, fh, left - int(self.origin[0]), top - int(self.origin[1]), w, h)

    def grab_into(self, left: int, top: int, w: int, h: int, dst: int) -> bool:
        _memmove_into(dst, self.grab(left, top, w, h))
        return True

    def close(self) -> None:
        pass  # ponytail: the session is shared for the process (shared_portal), never torn down


_portal: PortalGrabber | None = None
_portal_lock = threading.Lock()


def shared_portal() -> PortalGrabber:
    global _portal
    with _portal_lock:
        if _portal is None:
            _portal = PortalGrabber()
        return _portal


def linux_grabber() -> Any:
    return shared_portal() if is_wayland() else MssGrabber()


# --- macOS -------------------------------------------------------------------------------

SCREEN_RECORDING_SETTINGS = "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
SCREEN_RECORDING_NOTE = ("UEFN Ducky needs the Screen Recording permission to capture your screen. "
                         "Turn it on in System Settings > Privacy & Security > Screen Recording, then try again.")


def mac_screen_access() -> bool:
    """True when this app may read the screen. The first time macOS shows its own prompt."""
    try:
        import Quartz  # type: ignore[import-not-found]
    except ImportError:
        return True  # no pyobjc: mss reports the failure itself
    if Quartz.CGPreflightScreenCaptureAccess():
        return True
    Quartz.CGRequestScreenCaptureAccess()
    return False


def offer_screen_recording_settings() -> bool:
    """The permission note with a button to System Settings' Screen Recording pane; True when opened."""
    from frontend.mac_alert import ask

    if ask("Screen Recording is off", SCREEN_RECORDING_NOTE, ("Open System Settings", "Not now")) != 0:
        return False
    subprocess.run(["open", SCREEN_RECORDING_SETTINGS], check=False, timeout=10)
    return True


def _sck_image(left: int, top: int, w: int, h: int) -> Any:
    """A CGImage of the rect (points, top-left origin) from ScreenCaptureKit; None before 15.2."""
    try:
        import Quartz  # type: ignore[import-not-found]
        import ScreenCaptureKit  # type: ignore[import-not-found]
    except ImportError:
        return None
    mgr = getattr(ScreenCaptureKit, "SCScreenshotManager", None)
    if mgr is None or not hasattr(mgr, "captureImageInRect_completionHandler_"):
        return None
    box: dict[str, Any] = {}
    done = threading.Event()

    def got(image: Any, _error: Any) -> None:
        box["image"] = image
        done.set()

    mgr.captureImageInRect_completionHandler_(Quartz.CGRectMake(left, top, w, h), got)
    return box.get("image") if done.wait(5.0) else None


def _sized_bgra(raw: bytes, size: tuple[int, int], stride: int, w: int, h: int) -> bytes:
    """w*h*4 BGRA from a frame of *size* with *stride* bytes a row: a Retina frame (2 px a
    point) is scaled to the point size the caller asked for."""
    if size == (w, h) and stride == w * 4:
        return bytes(raw)
    from PIL import Image

    img = Image.frombuffer("RGBA", size, bytes(raw), "raw", "BGRA", stride, 1)
    if size != (w, h):
        img = img.resize((w, h), Image.Resampling.BOX)  # area average: no ringing at edges
    return img.tobytes("raw", "BGRA")


class MacGrabber:
    """macOS: ScreenCaptureKit when it's there, else one ``mss`` handle kept between grabs."""

    # ponytail: an SCK still per frame (~30 ms); an SCStream is the upgrade if the phone
    # stream needs 60 fps on macOS.

    def __init__(self) -> None:
        if not mac_screen_access():
            raise PermissionError(SCREEN_RECORDING_NOTE)
        self._mss: Any = None

    def grab(self, left: int, top: int, w: int, h: int) -> bytes:
        image = _sck_image(left, top, w, h)
        if image is not None:
            import Quartz  # type: ignore[import-not-found]

            data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(image))
            size = (int(Quartz.CGImageGetWidth(image)), int(Quartz.CGImageGetHeight(image)))
            return _sized_bgra(bytes(data), size, int(Quartz.CGImageGetBytesPerRow(image)), w, h)
        if self._mss is None:
            import mss

            self._mss = mss.mss()
        shot = self._mss.grab({"left": left, "top": top, "width": w, "height": h})
        return _sized_bgra(shot.raw, tuple(shot.size), int(shot.size[0]) * 4, w, h)

    def grab_into(self, left: int, top: int, w: int, h: int, dst: int) -> bool:
        _memmove_into(dst, self.grab(left, top, w, h))
        return True

    def close(self) -> None:
        if self._mss is not None:
            self._mss.close()


if sys.platform == "darwin":
    _BestGrabber = MacGrabber  # type: ignore[misc,assignment]  # noqa: F811
elif sys.platform != "win32":
    # Off Windows the best grabber is the Linux one. One name for every caller (and every
    # test that patches _BestGrabber), so Capturer looks it up the same way on both.
    _BestGrabber = linux_grabber  # type: ignore[misc,assignment]  # noqa: F811


def x11_windows() -> list[dict[str, Any]]:
    """Mapped EWMH client windows (``_NET_CLIENT_LIST``): xid, pid, title, cls, rect, transient_for,
    modal. [] without an X server (XWayland lists only X clients)."""
    try:
        from Xlib import X, display

        d = display.Display()
    except Exception:
        return []
    try:
        root = d.screen().root
        atom = d.intern_atom
        clients = root.get_full_property(atom("_NET_CLIENT_LIST"), X.AnyPropertyType)
        utf8, modal = atom("UTF8_STRING"), atom("_NET_WM_STATE_MODAL")
        out: list[dict[str, Any]] = []
        for xid in (clients.value if clients else []):
            w = d.create_resource_object("window", int(xid))
            try:
                if w.get_attributes().map_state != X.IsViewable:
                    continue
                geo = w.get_geometry()
                pos = root.translate_coords(w, 0, 0)
                pid = w.get_full_property(atom("_NET_WM_PID"), X.AnyPropertyType)
                name = w.get_full_property(atom("_NET_WM_NAME"), utf8)
                title = name.value.decode("utf-8", "replace") if name else (w.get_wm_name() or "")
                state = w.get_full_property(atom("_NET_WM_STATE"), X.AnyPropertyType)
                trans = w.get_wm_transient_for()
                cls = w.get_wm_class() or ("", "")
            except Exception:
                continue  # closed while we looked
            out.append({
                "xid": int(xid), "pid": int(pid.value[0]) if pid else 0, "title": str(title)[:200],
                "cls": cls[1] if len(cls) > 1 else "", "transient_for": int(trans.id) if trans else 0,
                "modal": bool(state and modal in state.value),
                "rect": {"left": pos.x, "top": pos.y, "right": pos.x + geo.width, "bottom": pos.y + geo.height,
                         "width": geo.width, "height": geo.height},
            })
        return out
    finally:
        d.close()


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
