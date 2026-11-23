"""Linux: pywebview GTK (WebKitGTK) client-side decorations for the frameless main window.

WebKitGTK has no -webkit-app-region and the panel only takes its native chrome path on
edgechromium, so a document-start script hands header drags and the WindowResize grips to
GTK begin_move_drag / begin_resize_drag (X11 and Wayland: the compositor owns the move, so
snapping and tiling work). It runs before React's listeners and stops the JS
set_window_bounds fallback in WindowDrag/WindowResize.tsx. ``win_frameless`` re-exports
these names; the Windows code there never calls into this module.
"""

from __future__ import annotations

import os
import sys

# ponytail: selectors copied from web/src/utils/windowDrag.ts; a test keeps them in step.
LINUX_CHROME_JS = r"""
(function () {
  if (window.__uefnLinuxChrome) return;
  var host = location.hostname;
  if (host !== "127.0.0.1" && host !== "localhost" && location.protocol !== "file:") return;
  window.__uefnLinuxChrome = true;
  var DRAG = ".drag-region, .pywebview-drag-region, .focus-drag-bar, .focus-header-reveal-band";
  var SKIP = ".dropdown-panel, .no-drag, button, input, textarea, select, option, a, label, " +
    "summary, [role='button'], [contenteditable='true']";
  var lastDown = 0;
  function bridge(name, params) {
    var pw = window.pywebview;
    if (pw && pw._jsApiCallback) pw._jsApiCallback(name, params, "uefn-chrome");
  }
  function isDrag(t) {
    if (!(t instanceof Element) || t.closest(SKIP)) return false;
    var focus = t.closest(".focus-shell");
    if (!focus && t.closest(".selectable-text")) return false;
    if (focus && t.closest(".verse-editor-host")) return false;
    return !!t.closest(DRAG);
  }
  addEventListener("pointerdown", function (e) {
    if (e.button !== 0 || !(e.target instanceof Element)) return;
    var grip = e.target.closest(".window-resize-grip");
    if (grip) {
      var m = /window-resize-grip--(nw|ne|sw|se|n|s|e|w)\b/.exec(grip.className);
      if (!m) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      bridge("uefnNativeWindowResize", [m[1], false, e.screenX, e.screenY]);
      return;
    }
    if (!isDrag(e.target)) return;
    e.stopImmediatePropagation();
    var now = Date.now();
    if (now - lastDown < 400) {
      lastDown = 0;
      bridge("uefnToggleMaximize", []);
      return;
    }
    lastDown = now;
    bridge("uefnNativeWindowMove", [e.screenX, e.screenY]);
  }, true);
  // Our pointerdown already toggled maximise; keep WindowDrag's dblclick from toggling back.
  addEventListener("dblclick", function (e) { if (isDrag(e.target)) e.stopImmediatePropagation(); }, true);
})();
"""

_GDK_EDGES = {
    "n": "NORTH",
    "s": "SOUTH",
    "e": "EAST",
    "w": "WEST",
    "nw": "NORTH_WEST",
    "ne": "NORTH_EAST",
    "sw": "SOUTH_WEST",
    "se": "SOUTH_EAST",
}


def _gtk_window(window: object):
    """The Gtk.Window behind a pywebview GTK window (``window.native``), else None."""
    native = getattr(window, "native", None)
    return native if native is not None and hasattr(native, "begin_move_drag") else None


def _gtk_begin_drag(window: object, edge: str | None, screen_x, screen_y) -> bool:
    """Start a compositor move (edge None) or resize while the left button is still down.

    Runs on the GTK main thread (the JS bridge signal does). Timestamp 0 = GDK_CURRENT_TIME;
    on Wayland GDK uses the pointer's implicit-grab serial and ignores the coordinates.
    """
    gw = _gtk_window(window)
    if gw is None:
        return False
    try:
        x, y = int(screen_x or 0), int(screen_y or 0)
    except (TypeError, ValueError):
        x = y = 0
    try:
        if edge is None:
            gw.begin_move_drag(1, x, y, 0)
            return True
        name = _GDK_EDGES.get(edge)
        if name is None:
            return False
        from gi.repository import Gdk

        gw.begin_resize_drag(getattr(Gdk.WindowEdge, name), 1, x, y, 0)
        return True
    except Exception:
        return False


def _gtk_toggle_maximize(window: object) -> bool:
    gw = _gtk_window(window)
    if gw is None:
        return False
    try:
        if gw.is_maximized():
            gw.unmaximize()
        else:
            gw.maximize()
        return True
    except Exception:
        return False


def linux_chrome_call(window: object, func_name: str, param: object) -> bool:
    """Run one LINUX_CHROME_JS bridge call; False when ``func_name`` is not one of ours."""
    from frontend.ui_web import win_frameless  # the shared move/resize entry points

    args = list(param) if isinstance(param, (list, tuple)) else []
    if func_name == "uefnNativeWindowMove":
        win_frameless.begin_native_window_move(window, *(args + [None, None])[:2])
        return True
    if func_name == "uefnNativeWindowResize":
        edge, double_click, sx, sy = (args + ["", False, None, None])[:4]
        win_frameless.begin_native_window_resize(
            window, str(edge or ""), double_click=double_click is True, screen_x=sx, screen_y=sy,
        )
        return True
    if func_name == "uefnToggleMaximize":
        _gtk_toggle_maximize(window)
        return True
    return False


def use_system_decorations() -> bool:
    """Linux: let the desktop draw its own title bar (e.g. KDE users who want KWin's).

    ``UEFN_DUCKY_SYSTEM_DECORATIONS=1``/``0`` wins; else the ``linux_system_decorations``
    panel setting when present. Off by default: our header is the title bar.
    """
    if not sys.platform.startswith("linux"):
        return False
    raw = os.environ.get("UEFN_DUCKY_SYSTEM_DECORATIONS", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    try:
        from frontend.settings import PanelSettings

        return bool(getattr(PanelSettings.load(), "linux_system_decorations", False))
    except Exception:
        return False


def _install_gtk_chrome_script() -> None:
    """Add LINUX_CHROME_JS at document start to every pywebview GTK window."""
    if getattr(_install_gtk_chrome_script, "_installed", False):
        return
    try:
        from webview.platforms import gtk as webview_gtk
    except Exception:
        return
    original_init = webview_gtk.BrowserView.__init__

    def __init__(self, window) -> None:
        original_init(self, window)
        try:
            webkit = webview_gtk.webkit
            self.manager.add_script(
                webkit.UserScript(
                    LINUX_CHROME_JS,
                    webkit.UserContentInjectedFrames.TOP_FRAME,
                    webkit.UserScriptInjectionTime.START,
                    None,
                    None,
                )
            )
        except Exception:
            pass

    webview_gtk.BrowserView.__init__ = __init__
    _install_gtk_chrome_script._installed = True  # type: ignore[attr-defined]
