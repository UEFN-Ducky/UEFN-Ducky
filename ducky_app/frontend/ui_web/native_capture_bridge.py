"""Native capture frames into the main panel page through a WebView2 SharedBuffer.

Per watched view: a 2-slot BGRA buffer posted once (again when the size changes) with
additionalData {view, w, h, slots}; the capture thread grabs each frame straight into slot
seq % 2, then a {type:"native-frame", view, seq, slot, w, h, fps} web message says it is ready.
The page (remote/nativeCapture.ts) turns it into a VideoFrame for the WebRTC sender.

The UI thread does one tiny post per frame and never more than one per view is queued: a
frame due while the last post still waits for the UI thread is skipped, and that lag slows
the capturer down. Nothing on the UI thread ever waits for the capture thread.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable

from frontend.native_capture import Capturer

_log = logging.getLogger(__name__)
_lock = threading.Lock()
_host: dict[str, Any] = {}  # core, form of the main window
_views: dict[str, "_View"] = {}


def register(core: Any, form: Any) -> None:
    """The main window's CoreWebView2 (again after a browser rebuild)."""
    with _lock:
        _host["core"] = core
        _host["form"] = form


def _on_ui(fn: Callable[[], Any], wait: bool, timeout: float = 5.0) -> Any:
    """Run fn on the WebView2 UI thread.

    Never form.Invoke from a Python thread: it blocks holding the GIL while the UI thread
    needs the GIL to run fn, and the whole app freezes (seen Nov 1 2026). BeginInvoke + an
    Event (whose wait releases the GIL) instead.
    """
    from System import Action  # type: ignore

    form = _host["form"]
    out: list[Any] = []
    done = threading.Event()

    def run() -> None:
        try:
            out.append(fn())
        except Exception as exc:  # handed back to the waiting thread
            out.append(exc)
        finally:
            done.set()

    form.BeginInvoke(Action(run))
    if not wait:
        return None
    if not done.wait(timeout):
        raise TimeoutError("WebView2 UI thread busy")
    if isinstance(out[0], Exception):
        raise out[0]
    return out[0]


class _View:
    def __init__(self, view: str, fps: int, rect_fn: Callable[[], Any]) -> None:
        self.view = view
        self.buf: Any = None
        self.ptr = 0
        self.size = (0, 0)
        self.seq = 0
        self.inflight = False  # a native-frame post waits for the UI thread
        self.closed = False
        self._want: object = None
        self.cap = Capturer(rect_fn, fps, self._target, self._frame)

    def _post_buffer(self, w: int, h: int) -> None:
        token = self._want = object()

        def make() -> Any:
            if self._want is not token:
                return None  # the capture thread gave up waiting: don't leak a buffer
            try:
                from Microsoft.Web.WebView2.Core import CoreWebView2SharedBufferAccess  # type: ignore

                read_only: Any = CoreWebView2SharedBufferAccess.ReadOnly
            except ImportError:
                read_only = 0  # CoreWebView2SharedBufferAccess.ReadOnly (no .NET in tests)
            core = _host["core"]
            buf = core.Environment.CreateSharedBuffer(2 * w * h * 4)
            meta = json.dumps({"view": self.view, "w": w, "h": h, "slots": 2})
            core.PostSharedBufferToScript(buf, read_only, meta)
            # The buffer is an STA COM object: read its pointer here, on the UI thread. Touched
            # from the capture thread, the call marshals to the UI thread with the GIL held.
            return buf, int(buf.Buffer.ToInt64())

        old = self.buf
        try:
            self.buf, self.ptr = _on_ui(make, wait=True)
        except TimeoutError:
            self._want = None
            raise
        self.size = (w, h)
        if old is not None:
            _on_ui(lambda: _close(old), wait=False)

    def _target(self, w: int, h: int) -> int | None:
        """Capture thread: the slot for the next frame, None while the last post is queued."""
        if self.closed or self.inflight:
            return None
        if (w, h) != self.size:
            self._post_buffer(w, h)
        return self.ptr + (self.seq % 2) * w * h * 4

    def _frame(self, w: int, h: int) -> None:
        msg = json.dumps({"type": "native-frame", "view": self.view, "seq": self.seq, "slot": self.seq % 2, "w": w, "h": h, "fps": self.cap.fps})
        core = _host["core"]
        queued = time.perf_counter()

        def post() -> None:
            self.cap.report_lag(time.perf_counter() - queued)
            try:
                if not self.closed:
                    core.PostWebMessageAsJson(msg)
            finally:
                self.inflight = False

        self.inflight = True
        self.seq += 1
        try:
            _on_ui(post, wait=False)
        except Exception:
            self.inflight = False
            raise

    def stop(self) -> None:
        self.closed = True
        self.cap.stop()
        if self.cap.running:
            # Still inside a grab into the buffer (the UI thread was busy): closing it now
            # would free memory that thread writes to. It stops at its next frame; leave it.
            return
        if self.buf is not None:
            buf, self.buf = self.buf, None
            _on_ui(lambda: _close(buf), wait=False)


def _close(buf: Any) -> None:
    try:
        buf.Close()
    except Exception:
        pass


def start(view: str, fps: int = 30, rect_fn: Callable[[], Any] | None = None) -> dict[str, Any]:
    """Begin streaming `view` (an hwnd, "monitor:N" or "desktop") into the page."""
    view = str(view or "").strip()
    if not view:
        return {"ok": False, "error": "no view"}
    with _lock:
        if "core" not in _host:
            return {"ok": False, "error": "no main WebView2"}
        old = _views.pop(view, None)
    if old:
        old.stop()
    if rect_fn is None:
        from frontend.window_view import window_box

        def rect_fn() -> Any:
            b = window_box(view)
            # "in", not truthiness: a display left of the primary ends at right == 0.
            return (b["left"], b["top"], b["right"], b["bottom"]) if "right" in b else None

    first = rect_fn()
    if not first:
        return {"ok": False, "error": "view not on screen"}
    v = _View(view, fps, rect_fn)
    with _lock:
        _views[view] = v
    v.cap.start()
    return {"ok": True, "view": view}


def stop(view: str) -> bool:
    with _lock:
        v = _views.pop(str(view or "").strip(), None)
    if v:
        v.stop()
    return v is not None


def running() -> list[str]:
    with _lock:
        return [k for k, v in _views.items() if v.cap.running]
