"""Browser panes on macOS: WKWebViews over the app's own page, in its NSWindow.

The app window is pywebview's Cocoa window (``window.native`` is an ``NSWindow``; its page is
a WKWebView in the ``contentView``). Each pane is another WKWebView added to that contentView
above the page and framed to its own rect, so the page takes clicks everywhere else. The
counterpart of ``gtk_panes``: ``browser_overlay`` keeps its pane bookkeeping and rebinds its
UI-thread helpers, control creation, bounds, capture and script calls to the functions here
on macOS.

``pane.control`` is a :class:`View`: the WKWebView plus the few ``CoreWebView2`` calls the
shared code makes. Each vault profile gets its own ``WKWebsiteDataStore``: ``memory`` a
non-persistent one (nothing on disk, like InPrivate), ``saved`` a persistent one of its own.

An agent's pane off screen (``render_behind``) goes under the page at its last on-screen
size: still in the window, so WebKit keeps drawing it.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
import uuid
from typing import Any

_log = logging.getLogger("browser_overlay")

_stores: dict[str, Any] = {}  # vault mode -> its WKWebsiteDataStore
_stores_lock = threading.Lock()
_delegates: dict[str, Any] = {}  # pane_id -> its navigation delegate (WKWebView holds it weakly)
_ABOVE, _BELOW = 1, -1  # NSWindowAbove, NSWindowBelow
_CERT_ERRORS = range(-1206, -1200)  # NSURLErrorServerCertificate* / ClientCertificate*


def _kits() -> tuple[Any, Any, Any]:
    import AppKit  # type: ignore[import-not-found]
    import Foundation  # type: ignore[import-not-found]
    import WebKit  # type: ignore[import-not-found]

    return AppKit, Foundation, WebKit


def _bo() -> Any:
    from frontend.ui_web import browser_overlay

    return browser_overlay


# --- the Cocoa main loop ----------------------------------------------------------------


def on_ui_thread(_window: Any = None) -> bool:
    return bool(_kits()[1].NSThread.isMainThread())


def _call_after(fn: Any) -> None:
    from PyObjCTools import AppHelper  # type: ignore[import-not-found]

    AppHelper.callAfter(fn)


def ui(window: Any, fn: Any) -> None:
    """Run fn on the main loop without blocking the caller."""
    if getattr(window, "native", None) is None:
        return

    def run() -> None:
        try:
            fn()
        except Exception:
            _log.exception("browser pane UI op failed")

    if on_ui_thread():
        run()
    else:
        _call_after(run)


def ui_sync(window: Any, fn: Any, timeout_s: float = 2.0) -> Any:
    """Run fn on the main loop and return its result; raises after timeout_s."""
    if on_ui_thread():
        return fn()
    box: dict[str, Any] = {"v": None, "e": None}
    done = threading.Event()

    def run() -> None:
        try:
            box["v"] = fn()
        except Exception as exc:
            box["e"] = exc
        finally:
            done.set()

    _call_after(run)
    if not done.wait(float(timeout_s)):
        raise RuntimeError(f"UI sync timed out after {timeout_s}s")
    if box["e"] is not None:
        raise box["e"]
    return box["v"]


def _wait_async(window: Any, start: Any, timeout_s: float) -> tuple[Any, str]:
    """(result, error) of a completion-handler call: ``start(done)`` runs on the main loop and
    its handler calls ``done(value, err)``; waited for here, never on the loop."""
    if on_ui_thread():
        return None, "cannot wait on the UI thread"
    box: dict[str, Any] = {"v": None, "e": ""}
    ev = threading.Event()

    def done(value: Any = None, err: str = "") -> None:
        box["v"], box["e"] = value, err
        ev.set()

    def begin() -> None:
        try:
            start(done)
        except Exception as exc:
            done(None, str(exc))

    try:
        ui_sync(window, begin, timeout_s=2.0)
    except Exception as exc:
        return None, str(exc)
    if not ev.wait(timeout_s):
        return None, f"timed out after {timeout_s:.0f} s"
    return box["v"], box["e"]


# --- the host window --------------------------------------------------------------------


def host_view(content: Any) -> Any:
    """The app's own WKWebView in the contentView (the calibration reference), never a pane."""
    WebKit = _kits()[2]
    panes = {id(d.view) for d in _delegates.values() if getattr(d, "view", None) is not None}
    for view in list(content.subviews() or ()):
        if isinstance(view, WebKit.WKWebView) and id(view) not in panes:
            return view
    return None


def data_store(mode: str) -> Any:
    """The WKWebsiteDataStore of a vault profile: one per mode, made once."""
    _, Foundation, WebKit = _kits()
    with _stores_lock:
        store = _stores.get(mode)
        if store is not None:
            return store
        Store = WebKit.WKWebsiteDataStore
        if mode == "saved" and hasattr(Store, "dataStoreForIdentifier_"):  # macOS 14+
            ident = uuid.uuid5(uuid.NAMESPACE_URL, _bo().user_data_dir())  # stable per profile folder
            store = Store.dataStoreForIdentifier_(Foundation.NSUUID.alloc().initWithUUIDString_(str(ident)))
        elif mode == "saved":
            store = Store.defaultDataStore()
        else:
            store = Store.nonPersistentDataStore()  # nothing on disk, like InPrivate
        _stores[mode] = store
        return store


# --- the pane control -------------------------------------------------------------------


class View:
    """``pane.control`` on macOS. ``CoreWebView2`` is itself, so browser_overlay's shared
    navigate / command code runs unchanged."""

    def __init__(self, view: Any) -> None:
        self.view = view
        self.CoreWebView2 = self

    def Navigate(self, url: str) -> None:  # noqa: N802  (the WebView2 name the shared code calls)
        _, Foundation, _ = _kits()
        self.view.loadRequest_(Foundation.NSURLRequest.requestWithURL_(Foundation.NSURL.URLWithString_(url)))

    @property
    def CanGoBack(self) -> bool:  # noqa: N802
        return bool(self.view.canGoBack())

    @property
    def CanGoForward(self) -> bool:  # noqa: N802
        return bool(self.view.canGoForward())

    def GoBack(self) -> None:  # noqa: N802
        self.view.goBack()

    def GoForward(self) -> None:  # noqa: N802
        self.view.goForward()

    def Reload(self) -> None:  # noqa: N802
        self.view.reload()

    def Stop(self) -> None:  # noqa: N802
        self.view.stopLoading()

    def Dispose(self) -> None:  # noqa: N802
        """UI thread: off the window, and its delegate let go."""
        view, self.view = self.view, None
        if view is None:
            return
        view.setNavigationDelegate_(None)
        view.removeFromSuperview()
        for key, d in list(_delegates.items()):
            if getattr(d, "view", None) is view:
                _delegates.pop(key, None)


# Navigation events, kept apart from the ObjC delegate so they test without AppKit.


def on_policy(pane: Any, uri: str, new_window: bool) -> bool:
    """True to let the pane load *uri*. Popups / target=_blank open a new app tab instead."""
    bo = _bo()
    if new_window:
        if bo._is_allowed_url(uri):
            bo._push_page(pane.window, {"type": "browser_pane_new_window", "pane_id": pane.pane_id, "url": uri})
        return False
    if bo.is_app_ui_url(uri):
        pane.failed = bo._SELF_EMBED_MSG
        bo._push_state(pane)
        return False
    return bool(bo._is_allowed_url(uri))


def on_started(pane: Any) -> None:
    pane.loading, pane.failed, pane.cert_error, pane.cert_error_status = True, "", "", ""
    _bo()._push_state(pane)


def on_finished(pane: Any, view: Any) -> None:
    url = view.URL()
    pane.url = str(url.absoluteString() if url is not None else "")
    pane.title = str(view.title() or "")
    pane.can_back, pane.can_forward = bool(view.canGoBack()), bool(view.canGoForward())
    pane.loading = False
    _bo()._push_state(pane)


def on_failed(pane: Any, code: int, message: str) -> None:
    pane.loading = False
    if int(code) == -999:  # NSURLErrorCancelled: a newer navigation replaced this one
        _bo()._push_state(pane)
        return
    if int(code) in _CERT_ERRORS:
        # Like Chrome: never trust a bad certificate silently; WebKit already refused it.
        pane.cert_error = f"Certificate error on {pane.url or 'this page'}"
        pane.cert_error_status = str(code)
    pane.failed = pane.cert_error or message or "The page could not load"
    _bo()._push_state(pane)


def on_dead(pane: Any) -> None:
    pane.visible = False  # a dead view on top of the app would be a blank rectangle
    apply_bounds(pane)


_delegate_cls: dict[str, Any] = {}


def _delegate_class() -> Any:
    """The WKNavigationDelegate class, defined once per process (an ObjC class can't repeat)."""
    if "cls" in _delegate_cls:
        return _delegate_cls["cls"]
    import objc  # type: ignore[import-not-found]

    _, Foundation, _ = _kits()

    class DuckyPaneDelegate(Foundation.NSObject, protocols=[objc.protocolNamed("WKNavigationDelegate")]):  # type: ignore[misc, call-arg]
        def webView_decidePolicyForNavigationAction_decisionHandler_(self, view, action, handler):
            url = action.request().URL()
            uri = str(url.absoluteString() if url is not None else "")
            allow = on_policy(self.pane, uri, action.targetFrame() is None)
            handler(1 if allow else 0)  # WKNavigationActionPolicyAllow / Cancel

        def webView_didStartProvisionalNavigation_(self, view, _nav):
            on_started(self.pane)

        def webView_didFinishNavigation_(self, view, _nav):
            on_finished(self.pane, view)

        def webView_didFailProvisionalNavigation_withError_(self, view, _nav, error):
            on_failed(self.pane, error.code(), str(error.localizedDescription() or ""))

        def webView_didFailNavigation_withError_(self, view, _nav, error):
            on_failed(self.pane, error.code(), str(error.localizedDescription() or ""))

        def webViewWebContentProcessDidTerminate_(self, view):
            on_dead(self.pane)

    _delegate_cls["cls"] = DuckyPaneDelegate
    return DuckyPaneDelegate


def create_control(pane: Any) -> None:
    """UI thread: the pane's WKWebView, its delegate, and its first page."""
    AppKit, Foundation, WebKit = _kits()
    bo = _bo()
    if pane.control is not None:
        return
    if not bo._mode:
        bo._mode = "memory"  # opened from the UI thread, where nothing can be asked: stay private
    try:
        content = pane.window.native.contentView()
        config = WebKit.WKWebViewConfiguration.alloc().init()
        config.setWebsiteDataStore_(data_store(bo._mode))
        view = WebKit.WKWebView.alloc().initWithFrame_configuration_(Foundation.NSMakeRect(0, 0, 1, 1), config)
        delegate = _delegate_class().alloc().init()
        delegate.pane, delegate.view = pane, view
        _delegates[pane.pane_id] = delegate
        view.setNavigationDelegate_(delegate)
        control = View(view)
        pane.control = control
        content.addSubview_positioned_relativeTo_(view, _ABOVE, None)
        pane.ready = True
        pane.failed = ""
        target, pane.pending_url = pane.pending_url, ""
        apply_bounds(pane)
        if target and not bo._block_self_embed(pane, target):
            control.Navigate(target)
        bo._push_state(pane)
    except Exception:
        _log.exception("browser pane create failed")
        pane.control, pane.ready = None, False
        pane.failed = "The WKWebView could not be created"
        bo._push_state(pane)


def apply_bounds(pane: Any) -> None:
    """UI thread: pin the view to its CSS rect (scale = host view size / page viewport).
    The contentView's origin is bottom-left, so y counts up from the bottom."""
    _, Foundation, _ = _kits()
    bo = _bo()
    pane.apply_queued = False
    control = pane.control
    native = getattr(pane.window, "native", None)
    if control is None or control.view is None or native is None:
        return
    try:
        view = control.view
        content = native.contentView()
        host = host_view(content)
        size = content.frame().size
        x, y, w, h = pane.css_bounds
        vw, vh = pane.css_viewport
        sx = sy = 1.0
        ox, oy, top = 0.0, 0.0, float(size.height)
        if host is not None:
            hf = host.frame()
            if vw > 1 and vh > 1:
                sx, sy = bo.sane_scale(hf.size.width / vw, hf.size.height / vh)
            ox, oy = float(hf.origin.x), float(hf.origin.y)
            top = oy + float(hf.size.height)
        shown = bool(pane.visible and w > 2 and h > 2)
        behind = not shown and pane.behind_until > time.monotonic()
        if shown:
            pw, ph = max(round(w * sx), 1), max(round(h * sy), 1)
            content.addSubview_positioned_relativeTo_(view, _ABOVE, None)  # back on top of the page
            view.setFrame_(Foundation.NSMakeRect(round(ox + x * sx), round(top - y * sy - ph), pw, ph))
            pane.last_size = (pw, ph)
            view.setHidden_(False)
            if pane.take_focus:
                pane.take_focus = False
                native.makeFirstResponder_(view)
        elif behind:
            bw, bh = pane.last_size
            if min(bw, bh) <= 2:
                bw, bh = (int(size.width), int(size.height))
            content.addSubview_positioned_relativeTo_(view, _BELOW, host)
            view.setFrame_(Foundation.NSMakeRect(0, 0, max(bw, 320), max(bh, 240)))
            view.setHidden_(False)
        else:
            view.setHidden_(True)
    except Exception:
        _log.exception("browser pane bounds failed")


# --- capture, scripts, data -------------------------------------------------------------


def _open_pane(pane_id: str) -> Any:
    bo = _bo()
    with bo._lock:
        pane = bo._panes.get((pane_id or "").strip())
    return pane if pane is not None and pane.ready and pane.control is not None else None


def _encode(image: Any, fmt: str) -> bytes:
    AppKit = _kits()[0]
    rep = AppKit.NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
    kind = 3 if fmt == "jpeg" else 4  # NSBitmapImageFileTypeJPEG / PNG
    props = {AppKit.NSImageCompressionFactor: 0.85} if fmt == "jpeg" else {}
    data = rep.representationUsingType_properties_(kind, props)
    return bytes(data) if data is not None else b""


def _capture(pane: Any, fmt: str, timeout_s: float) -> tuple[bytes, str]:
    """(image bytes, '') of what the view shows."""
    # ponytail: the visible rect only; a full-page capture needs a scroll-and-stitch.
    if on_ui_thread():
        return b"", "a capture cannot wait on the UI thread"

    def start(done: Any) -> None:
        def got(image: Any, error: Any) -> None:
            if image is None:
                done(None, str(error.localizedDescription() if error is not None else "no picture"))
                return
            try:
                data = _encode(image, fmt)
                done(data, "" if data else "could not encode the picture")
            except Exception as exc:
                done(None, str(exc))

        pane.control.view.takeSnapshotWithConfiguration_completionHandler_(None, got)

    data, err = _wait_async(pane.window, start, timeout_s)
    return (data or b""), (err or ("" if data else "empty picture"))


def snapshot(pane_id: str, timeout_s: float = 3.0) -> dict[str, Any]:
    """A JPEG of what a pane shows (data URL), like the Windows CapturePreview."""
    import base64

    pane = _open_pane(pane_id)
    if pane is None:
        return {"ok": False, "error": "pane not open"}
    data, err = _capture(pane, "jpeg", timeout_s)
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "data": "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")}


def _run_script(pane: Any, body: str, timeout_s: float) -> tuple[Any, str]:
    """Await an async function body in the page; its JSON-able result comes back as JSON."""
    WebKit = _kits()[2]

    def start(done: Any) -> None:
        def got(result: Any, error: Any) -> None:
            if error is not None:
                done(None, str(error.localizedDescription() or "script error")[:500])
                return
            try:
                done(json.loads(str(result)) if result is not None else None, "")
            except ValueError:
                done(None, "the script's value isn't JSON")

        pane.control.view.callAsyncJavaScript_arguments_inFrame_inContentWorld_completionHandler_(
            f"return JSON.stringify(await (async () => {{ {body} }})());", {}, None,
            WebKit.WKContentWorld.pageWorld(), got)

    return _wait_async(pane.window, start, timeout_s)


def evaluate(pane_id: str, expression: str, timeout_s: float = 30.0) -> dict[str, Any]:
    """Run a JavaScript expression in a pane's page and await its promise; same rules and
    answer as the Windows DevTools path: {"ok": True, "value": <JSON value>}."""
    bo = _bo()
    pane = _open_pane(pane_id)
    if pane is None:
        return {"ok": False, "error": "pane not open"}
    if not bo.script_allowed(pane.url):
        return {"ok": False, "error": "Scripts only run on sandbox localhost pages, YouTube and sites the person allowed"}
    value, err = _run_script(pane, f"return ({expression});", timeout_s)
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "value": value}


_NO_OP_METHODS = ("Emulation.setDeviceMetricsOverride", "Emulation.clearDeviceMetricsOverride",
                  "Emulation.setFocusEmulationEnabled")


def devtools(pane_id: str, method: str, params: dict[str, Any] | None = None, timeout_s: float = 30.0) -> dict[str, Any]:
    """The DevTools methods the Browser plugin's agent tools send, done with WKWebView; the
    Windows reply shape. Same rules as Windows: fixed methods only, on pages scripts may run on."""
    import base64

    bo = _bo()
    pane = _open_pane(pane_id)
    if pane is None:
        return {"ok": False, "error": "pane not open"}
    if not bo.script_allowed(pane.url):
        return {"ok": False, "error": "DevTools only runs on sandbox localhost pages, YouTube and sites the person allowed"}
    if on_ui_thread():
        return {"ok": False, "error": "a DevTools call cannot wait on the UI thread"}
    p = params or {}
    if method in _NO_OP_METHODS:
        return {"ok": True, "reply": {}}
    if method == "Page.captureScreenshot":
        data, err = _capture(pane, "png", timeout_s)
        return {"ok": False, "error": err} if err else {"ok": True, "reply": {"data": base64.b64encode(data).decode("ascii")}}
    if method == "Input.insertText":
        text = json.dumps(str(p.get("text") or ""))
        _v, err = _run_script(pane, f"document.execCommand('insertText', false, {text}); return null;", timeout_s)
        return {"ok": False, "error": err} if err else {"ok": True, "reply": {}}
    # ponytail: trusted mouse/key input needs NSEvent synthesis into the view; add it when the
    # Browser plugin's click tools are wanted on macOS.
    return {"ok": False, "error": f"DevTools {method} isn't on macOS (WKWebView has no DevTools channel)"}


def clear_browsing_data(kinds: str = "all") -> dict[str, Any]:
    """Clear the current profile's site data. *kinds*: all | cache | cookies | history."""
    _, Foundation, WebKit = _kits()
    bo = _bo()
    kind = (kinds or "all").strip().lower()
    if kind not in ("all", "cache", "cookies", "history"):
        return {"ok": False, "error": f"unknown kinds: {kinds!r}"}
    if kind == "history":
        # WKWebView keeps no history database (only each view's back/forward list).
        return {"ok": True, "via": "webkit", "kinds": kind}
    Store = WebKit.WKWebsiteDataStore
    types = {
        "all": Store.allWebsiteDataTypes(),
        "cache": Foundation.NSSet.setWithArray_([WebKit.WKWebsiteDataTypeDiskCache, WebKit.WKWebsiteDataTypeMemoryCache]),
        "cookies": Foundation.NSSet.setWithArray_([WebKit.WKWebsiteDataTypeCookies]),
    }[kind]
    window = bo._main_window
    if getattr(window, "native", None) is None:
        return {"ok": False, "error": "Ducky's window isn't open", "kinds": kind}

    def start(done: Any) -> None:
        data_store(bo._mode or "memory").removeDataOfTypes_modifiedSince_completionHandler_(
            types, Foundation.NSDate.distantPast(), lambda: done(True, ""))

    _ok, err = _wait_async(window, start, 8.0)
    if err:
        return {"ok": False, "error": err, "kinds": kind, "via": "webkit"}
    return {"ok": True, "via": "webkit", "kinds": kind}


def install(bo: Any) -> None:
    """Point browser_overlay's WebView2-only pieces at these (the same names gtk_panes binds)."""
    names = ("_ui", "_ui_sync", "_on_ui_thread", "_create_control", "_apply_bounds",
             "snapshot", "evaluate", "devtools", "clear_browsing_data")
    bo.__dict__.setdefault("_webview2", {n: getattr(bo, n) for n in names})
    bo._ui, bo._ui_sync, bo._on_ui_thread = ui, ui_sync, on_ui_thread
    bo._create_control, bo._apply_bounds = create_control, apply_bounds
    bo.snapshot, bo.evaluate, bo.devtools = snapshot, evaluate, devtools
    bo.clear_browsing_data = clear_browsing_data


# browser_overlay imports this module on macOS (its last lines).
if sys.platform == "darwin":
    install(_bo())
