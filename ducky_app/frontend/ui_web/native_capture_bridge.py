"""Native capture frames into the main panel page through a WebView2 SharedBuffer.

Per watched view: a 2-slot BGRA buffer posted once (again when the size changes) with
additionalData {view, w, h, slots}; each frame is copied into slot seq % 2 from the capture
thread, then a {type:"native-frame", view, seq, slot, w, h} web message says it is ready.
The page (remote/nativeCapture.ts) turns it into a VideoFrame for the WebRTC sender.
"""

from __future__ import annotations

import ctypes
import json
import logging
import threading
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
        self.cap = Capturer(rect_fn, fps, self._frame)

    def _post_buffer(self, w: int, h: int) -> None:
        def make() -> Any:
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
        self.buf, self.ptr = _on_ui(make, wait=True)
        self.size = (w, h)
        if old is not None:
            _on_ui(lambda: _close(old), wait=False)

    def _frame(self, data: bytes, w: int, h: int) -> None:
        if (w, h) != self.size:
            self._post_buffer(w, h)
        slot = self.seq % 2
        ctypes.memmove(self.ptr + slot * w * h * 4, data, len(data))
        msg = json.dumps({"type": "native-frame", "view": self.view, "seq": self.seq, "slot": slot, "w": w, "h": h})
        core = _host["core"]
        _on_ui(lambda: core.PostWebMessageAsJson(msg), wait=False)
        self.seq += 1

    def stop(self) -> None:
        self.cap.stop()
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
