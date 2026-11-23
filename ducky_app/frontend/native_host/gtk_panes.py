"""Browser panes on Linux: WebKitGTK views over the app's own page, in its Gtk.Window.

The app window is pywebview's GTK window (``window.native`` is a ``Gtk.Window``). The first
pane wraps that window's child in a ``Gtk.Overlay``; each pane is a ``WebKit2.WebView``
overlay child placed by its margins and sized by ``set_size_request``, so it covers only its
own rect (the page takes clicks everywhere else). ``browser_overlay`` keeps its pane
bookkeeping and rebinds its UI-thread helpers, control creation, bounds, capture and script
calls to the functions here on Linux; Windows never imports this module.

``pane.control`` is a :class:`View`: the WebKit view plus the few ``CoreWebView2`` calls
the shared code makes (``Navigate``, history, ``Reload``, ``Stop``, ``Dispose``).

An agent's pane off screen (``render_behind``) moves into a ``Gtk.OffscreenWindow`` at its
last on-screen size: overlay children always draw above the page, and a hidden view stops
drawing.
"""

from __future__ import annotations

import base64
import json
import logging
import sys
import threading
import time
from typing import Any

_log = logging.getLogger("browser_overlay")

_ctx: Any = None  # one WebKit2.WebContext for every pane (one cookie jar, like WebView2's env)
_ctx_lock = threading.Lock()
_overlays: dict[int, Any] = {}  # id(Gtk.Window) -> its Gtk.Overlay


def _gi() -> tuple[Any, Any, Any]:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import GLib, Gtk, WebKit2

    return GLib, Gtk, WebKit2


def _bo() -> Any:
    from frontend.ui_web import browser_overlay

    return browser_overlay


# --- the GTK main loop ------------------------------------------------------------------


def on_ui_thread(_window: Any = None) -> bool:
    GLib = _gi()[0]
    return bool(GLib.MainContext.default().is_owner())


def ui(window: Any, fn: Any) -> None:
    """Run fn on the GTK main loop without blocking the caller."""
    if getattr(window, "native", None) is None:
        return

    def run() -> bool:
        try:
            fn()
        except Exception:
            _log.exception("browser pane UI op failed")
        return False

    if on_ui_thread():
        run()
    else:
        _gi()[0].idle_add(run)


def ui_sync(window: Any, fn: Any, timeout_s: float = 2.0) -> Any:
    """Run fn on the GTK main loop and return its result; raises after timeout_s."""
    if on_ui_thread():
        return fn()
    box: dict[str, Any] = {"v": None, "e": None}
    done = threading.Event()

    def run() -> bool:
        try:
            box["v"] = fn()
        except Exception as exc:
            box["e"] = exc
        finally:
            done.set()
        return False

    _gi()[0].idle_add(run)
    if not done.wait(float(timeout_s)):
        raise RuntimeError(f"UI sync timed out after {timeout_s}s")
    if box["e"] is not None:
        raise box["e"]
    return box["v"]


def _wait_async(window: Any, start: Any, timeout_s: float) -> tuple[Any, str]:
    """(result, error) of a GIO async call: ``start(done)`` runs on the main loop and calls
    ``done(result)`` (or raises) from its callback; waited for here, never on the loop."""
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


def overlay_of(window: Any) -> Any:
    """The window's Gtk.Overlay, wrapping its child on first use. UI thread."""
    _, Gtk, _ = _gi()
    native = window.native
    ov = _overlays.get(id(native))
    if ov is not None and ov.get_parent() is native:
        return ov
    child = native.get_child()
    if isinstance(child, Gtk.Overlay):
        ov = child
    else:
        ov = Gtk.Overlay()
        if child is not None:
            native.remove(child)
            ov.add(child)
        native.add(ov)
        ov.show()
    ov.connect("size-allocate", _relayout)  # never a closure over the window (see _wire)
    _overlays[id(native)] = ov
    return ov


_last_size: dict[int, tuple[int, int]] = {}


def _relayout(ov: Any, rect: Any) -> None:
    """The overlay got a new size (first layout, window resize): place its panes again,
    after this allocation pass (margins set inside one would queue another)."""
    key, size = id(ov), (rect.width, rect.height)
    if _last_size.get(key) == size:
        return
    _last_size[key] = size
    bo = _bo()

    def run() -> bool:
        native = ov.get_parent()
        with bo._lock:
            panes = [p for p in bo._panes.values()
                     if p.control is not None and getattr(p.window, "native", None) is native]
        for p in panes:
            apply_bounds(p)
        return False

    _gi()[0].idle_add(run)


def host_view(ov: Any) -> Any:
    """The app's own WebKit view (the calibration reference), never a pane."""
    _, Gtk, WebKit2 = _gi()
    todo = [ov.get_child()]
    while todo:
        w = todo.pop(0)
        if isinstance(w, WebKit2.WebView):
            return w
        if isinstance(w, Gtk.Container):
            todo.extend(w.get_children())
    return None


def _context() -> Any:
    """The shared WebContext: ephemeral (memory mode) or on disk under the profile dir."""
    global _ctx
    _, _, WebKit2 = _gi()
    with _ctx_lock:
        if _ctx is not None:
            return _ctx
        bo = _bo()
        if bo._mode == "saved":
            root = bo.user_data_dir()
            mgr = WebKit2.WebsiteDataManager(base_data_directory=f"{root}/data", base_cache_directory=f"{root}/cache")
            ctx = WebKit2.WebContext.new_with_website_data_manager(mgr)
            ctx.get_cookie_manager().set_persistent_storage(
                f"{root}/cookies.sqlite", WebKit2.CookiePersistentStorage.SQLITE)
        else:
            ctx = WebKit2.WebContext.new_ephemeral()  # nothing on disk, like InPrivate
        _ctx = ctx
        return ctx


# --- the pane control -------------------------------------------------------------------


class View:
    """``pane.control`` on Linux. ``CoreWebView2`` is itself, so browser_overlay's shared
    navigate / command code runs unchanged."""

    def __init__(self, view: Any) -> None:
        self.view = view
        self.CoreWebView2 = self
        self.offscreen: Any = None

    def Navigate(self, url: str) -> None:  # noqa: N802  (the WebView2 name the shared code calls)
        self.view.load_uri(url)

    @property
    def CanGoBack(self) -> bool:  # noqa: N802
        return bool(self.view.can_go_back())

    @property
    def CanGoForward(self) -> bool:  # noqa: N802
        return bool(self.view.can_go_forward())

    def GoBack(self) -> None:  # noqa: N802
        self.view.go_back()

    def GoForward(self) -> None:  # noqa: N802
        self.view.go_forward()

    def Reload(self) -> None:  # noqa: N802
        self.view.reload()

    def Stop(self) -> None:  # noqa: N802
        self.view.stop_loading()

    def Dispose(self) -> None:  # noqa: N802
        """UI thread. Lets go of the GTK objects here too: the View itself may be freed on
        an RPC thread, and a WebKit object released off the GTK thread aborts the process."""
        self.view.destroy()
        if self.offscreen is not None:
            self.offscreen.destroy()
        self.view = self.offscreen = None


def _put(view: Any, parent: Any, overlay: bool) -> None:
    """Move view under parent (an overlay child, or the offscreen window's one child)."""
    if view.get_parent() is parent:
        return
    old = view.get_parent()
    if old is not None:
        old.remove(view)
    if overlay:
        parent.add_overlay(view)
    else:
        parent.add(view)


def create_control(pane: Any) -> None:
    """UI thread: the pane's WebKit view, its signals, and its first page."""
    _, Gtk, WebKit2 = _gi()
    bo = _bo()
    if pane.control is not None:
        return
    if not bo._mode:
        bo._mode = "memory"  # opened from the UI thread, where nothing can be asked: stay private
    try:
        ov = overlay_of(pane.window)
        view = WebKit2.WebView.new_with_context(_context())
        s = view.get_settings()
        s.set_enable_developer_extras(False)
        s.set_javascript_can_open_windows_automatically(False)
        view.set_halign(Gtk.Align.START)
        view.set_valign(Gtk.Align.START)
        control = View(view)
        _wire(pane, control)
        pane.control = control
        ov.add_overlay(view)
        pane.ready = True
        pane.failed = ""
        target, pane.pending_url = pane.pending_url, ""
        apply_bounds(pane)
        if target and not bo._block_self_embed(pane, target):
            view.load_uri(target)
        bo._push_state(pane)
    except Exception:
        _log.exception("browser pane create failed")
        pane.control, pane.ready = None, False
        pane.failed = "The WebKitGTK view could not be created (is gir1.2-webkit2-4.1 installed?)"
        bo._push_state(pane)


def _wire(pane: Any, control: View) -> None:
    """The view's signals. Callbacks use the view GTK hands them, never a captured one: a
    closure holding the view that holds the closure is a cycle through C, and Python's gc
    would break it on whatever thread it runs (a WebKit object freed there aborts)."""
    _, _, WebKit2 = _gi()
    bo = _bo()
    Decision = WebKit2.PolicyDecisionType

    def history(v: Any) -> None:
        pane.can_back, pane.can_forward = bool(v.can_go_back()), bool(v.can_go_forward())

    def on_uri(v: Any, *_a: Any) -> None:
        pane.url = str(v.get_uri() or "")
        history(v)
        bo._push_state(pane)

    def on_title(v: Any, *_a: Any) -> None:
        pane.title = str(v.get_title() or "")
        bo._push_state(pane)

    def on_load(v: Any, event: Any) -> None:
        if event == WebKit2.LoadEvent.STARTED:
            pane.loading, pane.failed, pane.cert_error, pane.cert_error_status = True, "", "", ""
        elif event == WebKit2.LoadEvent.FINISHED:
            pane.loading = False
        history(v)
        bo._push_state(pane)

    def on_policy(_v: Any, decision: Any, kind: Any) -> bool:
        if kind not in (Decision.NAVIGATION_ACTION, Decision.NEW_WINDOW_ACTION):
            return False
        uri = str(decision.get_navigation_action().get_request().get_uri() or "")
        if kind == Decision.NEW_WINDOW_ACTION:
            # Popups / target=_blank open a new app tab, as on Windows.
            decision.ignore()
            if bo._is_allowed_url(uri):
                bo._push_page(pane.window, {"type": "browser_pane_new_window", "pane_id": pane.pane_id, "url": uri})
            return True
        if bo.is_app_ui_url(uri):
            decision.ignore()
            pane.failed = bo._SELF_EMBED_MSG
            bo._push_state(pane)
            return True
        if not bo._is_allowed_url(uri):
            decision.ignore()
            return True
        return False

    def on_tls(_v: Any, uri: str, _cert: Any, errors: Any) -> bool:
        # Like Chrome: never trust a bad certificate silently; WebKit blocks the load.
        pane.cert_error = f"Certificate error on {uri or pane.url}"
        pane.cert_error_status = str(errors)
        pane.failed = pane.cert_error
        bo._push_state(pane)
        return False

    def on_dead(*_a: Any) -> None:
        pane.visible = False  # a dead view on top of the app would be a blank rectangle
        apply_bounds(pane)

    view = control.view
    view.connect("notify::uri", on_uri)
    view.connect("notify::title", on_title)
    view.connect("load-changed", on_load)
    view.connect("decide-policy", on_policy)
    view.connect("load-failed-with-tls-errors", on_tls)
    view.connect("web-process-terminated", on_dead)


def apply_bounds(pane: Any) -> None:
    """UI thread: pin the view to its CSS rect (scale = host view size / page viewport)."""
    _, Gtk, _ = _gi()
    bo = _bo()
    pane.apply_queued = False
    control = pane.control
    if control is None or getattr(pane.window, "native", None) is None:
        return
    try:
        view = control.view
        ov = overlay_of(pane.window)
        host = host_view(ov)
        x, y, w, h = pane.css_bounds
        vw, vh = pane.css_viewport
        sx = sy = 1.0
        ox = oy = 0
        if host is not None:
            a = host.get_allocation()
            if vw > 1 and vh > 1:
                sx, sy = bo.sane_scale(a.width / vw, a.height / vh)
            # An overlay not laid out yet sits at -1,-1 (offset 1,1): its size-allocate
            # places the panes again (_relayout).
            at = host.translate_coordinates(ov, 0, 0) if ov.get_allocated_width() > 1 else None
            if at:
                ox, oy = int(at[-2]), int(at[-1])
        shown = bool(pane.visible and w > 2 and h > 2)
        behind = not shown and pane.behind_until > time.monotonic()
        if shown:
            _put(view, ov, True)
            pw, ph = max(int(round(w * sx)), 1), max(int(round(h * sy)), 1)
            view.set_margin_start(max(int(round(ox + x * sx)), 0))
            view.set_margin_top(max(int(round(oy + y * sy)), 0))
            view.set_size_request(pw, ph)
            pane.last_size = (pw, ph)
            view.show()
            if pane.take_focus:
                pane.take_focus = False
                view.grab_focus()
        elif behind:
            bw, bh = pane.last_size
            if min(bw, bh) <= 2:
                bw, bh = (host.get_allocated_width(), host.get_allocated_height()) if host is not None else (1280, 800)
            if control.offscreen is None:
                control.offscreen = Gtk.OffscreenWindow()
            _put(view, control.offscreen, False)
            view.set_margin_start(0)
            view.set_margin_top(0)
            view.set_size_request(max(bw, 320), max(bh, 240))
            view.show()
            control.offscreen.show()
        else:
            _put(view, ov, True)
            view.hide()
    except Exception:
        _log.exception("browser pane bounds failed")


# --- capture, scripts, data -------------------------------------------------------------


def _gdk() -> Any:
    import gi

    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk

    return Gdk


def _open_pane(pane_id: str) -> Any:
    bo = _bo()
    with bo._lock:
        pane = bo._panes.get((pane_id or "").strip())
    return pane if pane is not None and pane.ready and pane.control is not None else None


def _capture(pane: Any, full: bool, fmt: str, timeout_s: float) -> tuple[bytes, str]:
    """(image bytes, '') of the view: what it shows, or the whole page (``full``)."""
    if on_ui_thread():
        return b"", "a capture cannot wait on the UI thread"

    def start(done: Any) -> None:
        _, _, WebKit2 = _gi()
        Gdk = _gdk()
        view = pane.control.view
        region = WebKit2.SnapshotRegion.FULL_DOCUMENT if full else WebKit2.SnapshotRegion.VISIBLE

        def got(_v: Any, res: Any) -> None:
            try:
                surface = view.get_snapshot_finish(res)
                pix = Gdk.pixbuf_get_from_surface(surface, 0, 0, surface.get_width(), surface.get_height())
                opts = (["quality"], ["85"]) if fmt == "jpeg" else ([], [])
                ok, buf = pix.save_to_bufferv(fmt, *opts)
                done(bytes(buf) if ok else b"", "" if ok else "could not encode the picture")
            except Exception as exc:
                done(None, str(exc))

        view.get_snapshot(region, WebKit2.SnapshotOptions.NONE, None, got)

    data, err = _wait_async(pane.window, start, timeout_s)
    return (data or b""), (err or ("" if data else "empty picture"))


def snapshot(pane_id: str, timeout_s: float = 3.0) -> dict[str, Any]:
    """A JPEG of what a pane shows (data URL), like the Windows CapturePreview."""
    pane = _open_pane(pane_id)
    if pane is None:
        return {"ok": False, "error": "pane not open"}
    data, err = _capture(pane, False, "jpeg", timeout_s)
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "data": "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")}


def evaluate(pane_id: str, expression: str, timeout_s: float = 30.0) -> dict[str, Any]:
    """Run a JavaScript expression in a pane's page and await its promise; same rules and
    answer as the Windows DevTools path: {"ok": True, "value": <JSON value>}."""
    bo = _bo()
    pane = _open_pane(pane_id)
    if pane is None:
        return {"ok": False, "error": "pane not open"}
    if not bo.script_allowed(pane.url):
        return {"ok": False, "error": "Scripts only run on sandbox localhost pages, YouTube and sites the person allowed"}

    def start(done: Any) -> None:
        view = pane.control.view

        def got(_v: Any, res: Any) -> None:
            try:
                value = view.call_async_javascript_function_finish(res)
                raw = value.to_json(0) if value is not None and not value.is_undefined() else None
                done(json.loads(raw) if raw else None, "")
            except Exception as exc:
                done(None, str(getattr(exc, "message", exc))[:500] or "script error")

        view.call_async_javascript_function(f"return ({expression});", -1, None, None, None, None, got)

    value, err = _wait_async(pane.window, start, timeout_s)
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "value": value}


# --- DevTools methods, on WebKitGTK -----------------------------------------------------

# A full-document snapshot already covers the whole page, and a pane's view takes input
# without the window in front: these change nothing here.
_NO_OP_METHODS = ("Emulation.setDeviceMetricsOverride", "Emulation.clearDeviceMetricsOverride",
                  "Emulation.setFocusEmulationEnabled")
# DevTools key names -> GDK keyval names (single characters map through their code point).
_KEYVALS = {
    "Enter": "Return", "Tab": "Tab", "Escape": "Escape", "Backspace": "BackSpace", "Delete": "Delete",
    " ": "space", "ArrowUp": "Up", "ArrowDown": "Down", "ArrowLeft": "Left", "ArrowRight": "Right",
    "PageUp": "Page_Up", "PageDown": "Page_Down", "Home": "Home", "End": "End",
}
_BUTTONS = {"left": 1, "middle": 2, "right": 3}


def _modifiers(bits: int) -> Any:
    """DevTools modifiers (1 Alt, 2 Ctrl, 4 Meta, 8 Shift) as a GDK state mask."""
    M = _gdk().ModifierType
    state = M(0)
    for bit, mask in ((1, M.MOD1_MASK), (2, M.CONTROL_MASK), (4, M.META_MASK), (8, M.SHIFT_MASK)):
        if int(bits or 0) & bit:
            state |= mask
    return state


def _send(view: Any, ev: Any, device: Any) -> None:
    """UI thread: one GDK event straight into the view, as the window system would hand it
    (trusted input for the page). ``window`` is only lent: GDK frees an event's window."""
    win = view.get_window()
    if win is None:
        raise RuntimeError("the pane isn't on screen yet")
    ev.window = win
    ev.send_event = 0
    ev.time = 0
    if device is not None:
        ev.set_device(device)
    try:
        view.event(ev)
    finally:
        ev.window = None


def _mouse(view: Any, p: dict[str, Any]) -> str:
    Gdk = _gdk()
    T = Gdk.EventType
    kind = str(p.get("type") or "")
    zoom = float(view.get_zoom_level() or 1.0)
    x, y = float(p.get("x") or 0) * zoom, float(p.get("y") or 0) * zoom
    seat = view.get_display().get_default_seat()
    pointer = seat.get_pointer() if seat is not None else None
    if kind == "mouseWheel":
        # ponytail: a scroll by script, not a wheel event; the plugin only scrolls the page.
        dx, dy = float(p.get("deltaX") or 0), float(p.get("deltaY") or 0)
        view.evaluate_javascript(f"window.scrollBy({dx}, {dy})", -1, None, None, None, None, None)
        return ""
    if kind == "mouseMoved":
        ev = Gdk.Event.new(T.MOTION_NOTIFY)
        ev.x, ev.y, ev.state = x, y, _modifiers(p.get("modifiers") or 0)
        _send(view, ev, pointer)
        return ""
    if kind not in ("mousePressed", "mouseReleased"):
        return f"Input.dispatchMouseEvent type {kind!r} isn't on Linux"
    button = _BUTTONS.get(str(p.get("button") or "left"), 1)
    presses = [T.BUTTON_PRESS] + ([T.DOUBLE_BUTTON_PRESS] if int(p.get("clickCount") or 1) >= 2 else []) \
        if kind == "mousePressed" else [T.BUTTON_RELEASE]
    for t in presses:
        ev = Gdk.Event.new(t)
        ev.x, ev.y, ev.button, ev.state = x, y, button, _modifiers(p.get("modifiers") or 0)
        _send(view, ev, pointer)
    return ""


def _key(view: Any, p: dict[str, Any]) -> str:
    Gdk = _gdk()
    kind = str(p.get("type") or "")
    if kind not in ("keyDown", "rawKeyDown", "keyUp", "char"):
        return f"Input.dispatchKeyEvent type {kind!r} isn't on Linux"
    if kind != "keyUp" and "selectAll" in (p.get("commands") or []):
        view.execute_editing_command("SelectAll")
        return ""
    if kind == "char":
        return ""  # keyDown with text already typed it (the IM commits a printable key's text)
    key = str(p.get("key") or "")
    keyval = Gdk.keyval_from_name(_KEYVALS[key]) if key in _KEYVALS else (
        Gdk.unicode_to_keyval(ord(key)) if len(key) == 1 else 0)
    if not keyval:
        return f"Input.dispatchKeyEvent key {key!r} isn't on Linux"
    ev = Gdk.Event.new(Gdk.EventType.KEY_RELEASE if kind == "keyUp" else Gdk.EventType.KEY_PRESS)
    ev.keyval, ev.state = keyval, _modifiers(p.get("modifiers") or 0)
    found, keys = Gdk.Keymap.get_for_display(view.get_display()).get_entries_for_keyval(keyval)
    if found and keys:
        ev.hardware_keycode, ev.group = keys[0].keycode, keys[0].group
    seat = view.get_display().get_default_seat()
    _send(view, ev, seat.get_keyboard() if seat is not None else None)
    return ""


def devtools(pane_id: str, method: str, params: dict[str, Any] | None = None, timeout_s: float = 30.0) -> dict[str, Any]:
    """The DevTools methods the Browser plugin's agent tools send, done with WebKitGTK; the
    Windows reply shape: {"ok": True, "reply": {...}} (screenshots: reply["data"], base64 PNG).
    Same rules as Windows: plugin code with fixed methods only, on pages scripts may run on."""
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
        data, err = _capture(pane, True, "png", timeout_s)
        return {"ok": False, "error": err} if err else {"ok": True, "reply": {"data": base64.b64encode(data).decode("ascii")}}
    # The view is read on the UI thread: never a WebKit wrapper held on this RPC thread.
    if method == "Input.dispatchMouseEvent":
        act = lambda: _mouse(pane.control.view, p)  # noqa: E731
    elif method == "Input.dispatchKeyEvent":
        act = lambda: _key(pane.control.view, p)  # noqa: E731
    elif method == "Input.insertText":
        act = lambda: pane.control.view.execute_editing_command_with_argument(  # noqa: E731
            "InsertText", str(p.get("text") or "")) or ""
    else:
        return {"ok": False, "error": f"DevTools {method} isn't on Linux (WebKitGTK has no DevTools channel)"}
    try:
        why = ui_sync(pane.window, act, timeout_s=min(float(timeout_s), 10.0))
    except Exception as exc:
        return {"ok": False, "error": f"{method} failed: {exc}"}
    return {"ok": False, "error": why} if why else {"ok": True, "reply": {}}


def clear_browsing_data(kinds: str = "all") -> dict[str, Any]:
    """Clear the shared WebContext's site data. *kinds*: all | cache | cookies | history."""
    _, _, WebKit2 = _gi()
    bo = _bo()
    kind = (kinds or "all").strip().lower()
    if kind not in ("all", "cache", "cookies", "history"):
        return {"ok": False, "error": f"unknown kinds: {kinds!r}"}
    if kind == "history":
        # WebKitGTK keeps no history database (only each view's back/forward list).
        return {"ok": True, "via": "webkit", "kinds": kind}
    T = WebKit2.WebsiteDataTypes
    types = {"all": T.ALL, "cache": T.MEMORY_CACHE | T.DISK_CACHE, "cookies": T.COOKIES}[kind]
    window = bo._main_window
    if getattr(window, "native", None) is None:
        return {"ok": False, "error": "Ducky's window isn't open", "kinds": kind}

    def start(done: Any) -> None:
        mgr = _context().get_website_data_manager()
        mgr.clear(types, 0, None, lambda m, res: done(bool(m.clear_finish(res)), ""))

    _ok, err = _wait_async(window, start, 8.0)
    if err:
        return {"ok": False, "error": err, "kinds": kind, "via": "webkit"}
    return {"ok": True, "via": "webkit", "kinds": kind}


def install(bo: Any) -> None:
    """Point browser_overlay's WebView2-only pieces at these. Its pane bookkeeping,
    open/close/bounds/navigate/back/forward code then runs as it is on Windows."""
    bo._ui, bo._ui_sync, bo._on_ui_thread = ui, ui_sync, on_ui_thread
    bo._create_control, bo._apply_bounds = create_control, apply_bounds
    bo.snapshot, bo.evaluate, bo.devtools = snapshot, evaluate, devtools
    bo.clear_browsing_data = clear_browsing_data


# browser_overlay imports this module on Linux (its last lines); either import order lands here
# once both modules are defined.
if sys.platform.startswith("linux"):
    install(_bo())
