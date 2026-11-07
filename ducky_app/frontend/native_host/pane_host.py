"""Browser panes under Ducky-Launcher.exe: a WinForms form of this process, made a child
window of the launcher's main window, holds the panes' WebView2 controls.

``browser_overlay`` pins its controls into ``window.native`` (in the Python window that is
pywebview's own form). In ``--backend`` mode there is no such form, so ``PaneHost`` stands
in: ``native`` is a borderless form on its own STA thread, parented across processes into
the launcher window (``DuckyLauncherWindow``) and kept the size of its client area. While a
pane is on screen the form sits above the page, clipped to the panes (the page shows and
takes clicks everywhere else); otherwise it sits under the page. Pane state goes to the
page through the panel event stream.

ponytail: the form lives as long as the launcher window it was put in; a window the
launcher makes again (closed to the tray, then opened) gets a new form on the next pane
open, and panes opened before that are gone (the page opens them again).
"""

from __future__ import annotations

import ctypes
import functools
import logging
import threading
from ctypes import wintypes
from typing import Any, Callable

_log = logging.getLogger(__name__)

LAUNCHER_CLASS = "DuckyLauncherWindow"
_GWL_STYLE = -16
_WS_CHILD = 0x40000000
_WS_POPUP = 0x80000000
_WS_CAPTION = 0x00C00000
_WS_THICKFRAME = 0x00040000
_WS_CLIPSIBLINGS = 0x04000000
_WS_CLIPCHILDREN = 0x02000000
_SWP_NOACTIVATE = 0x0010
_SWP_SHOWWINDOW = 0x0040
_HWND_TOP = 0
_HWND_BOTTOM = 1
_RGN_OR = 2
_DPI_PER_MONITOR_V2 = -4
_START_WAIT_S = 15.0

_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


@functools.cache
def _user32() -> Any:
    """Our own user32 with argtypes (64-bit handles), never ctypes.windll's shared one."""
    u = ctypes.WinDLL("user32", use_last_error=True)
    H, Int = wintypes.HWND, ctypes.c_int
    u.EnumWindows.argtypes = [_ENUM_PROC, wintypes.LPARAM]
    u.GetWindowThreadProcessId.argtypes = [H, ctypes.POINTER(wintypes.DWORD)]
    u.GetClassNameW.argtypes = [H, wintypes.LPWSTR, Int]
    u.IsWindow.argtypes = [H]
    u.GetClientRect.argtypes = [H, ctypes.POINTER(wintypes.RECT)]
    u.GetWindowLongPtrW.argtypes = [H, Int]
    u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    u.SetWindowLongPtrW.argtypes = [H, Int, ctypes.c_ssize_t]
    u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    u.SetParent.argtypes = [H, H]
    u.SetParent.restype = H
    u.SetWindowPos.argtypes = [H, H, Int, Int, Int, Int, wintypes.UINT]
    u.GetParent.argtypes = [H]
    u.GetParent.restype = H
    u.SetWindowRgn.argtypes = [H, wintypes.HRGN, wintypes.BOOL]
    u.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_ssize_t]
    u.SetThreadDpiAwarenessContext.restype = ctypes.c_ssize_t
    return u


@functools.cache
def _gdi32() -> Any:
    g = ctypes.WinDLL("gdi32")
    Int = ctypes.c_int
    g.CreateRectRgn.argtypes = [Int, Int, Int, Int]
    g.CreateRectRgn.restype = wintypes.HRGN
    g.CombineRgn.argtypes = [wintypes.HRGN, wintypes.HRGN, wintypes.HRGN, Int]
    g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    return g


def find_launcher_window(pid: int, class_name: str = LAUNCHER_CLASS) -> int:
    """The top-level window of class ``class_name`` that process ``pid`` owns, or 0."""
    user32 = _user32()
    found: list[int] = []

    def each(hwnd: Any, _lp: Any) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid:
            return True
        buf = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, buf, 64)
        if buf.value == class_name:
            found.append(int(hwnd))
            return False
        return True

    user32.EnumWindows(_ENUM_PROC(each), 0)
    return found[0] if found else 0


def child_style(style: int) -> int:
    """A top-level form's style as a clipped child window (no caption, frame or popup bit)."""
    style &= ~(_WS_POPUP | _WS_CAPTION | _WS_THICKFRAME)
    return style | _WS_CHILD | _WS_CLIPSIBLINGS | _WS_CLIPCHILDREN


def shown_rects(controls: list[Any], form: Any) -> list[tuple[int, int, int, int]]:
    """(x, y, w, h) of the controls on screen in ``form`` (what the form is clipped to).

    ``==``, never ``is``: pythonnet wraps ``c.Parent`` in a new Python object on every read,
    so ``is`` was always False, no pane counted as shown and the form sat under the page for
    good (black Browser page, clicks to the page; only hover-card snapshots showed it)."""
    out = []
    for c in controls:
        try:
            if bool(c.Visible) and c.Parent == form and int(c.Width) > 2 and int(c.Height) > 2:
                out.append((int(c.Left), int(c.Top), int(c.Width), int(c.Height)))
        except Exception:
            pass
    return out


class PaneHost:
    """What ``browser_overlay`` takes for a window: ``native`` (the form), ``client_size()``
    (the launcher's client area in pixels), ``after_bounds()`` and ``push_event()``."""

    is_launcher_host = True

    def __init__(self, launcher_pid: int, *, class_name: str = LAUNCHER_CLASS,
                 push: Callable[[dict[str, Any]], None] | None = None) -> None:
        self._pid = int(launcher_pid)
        self._class = class_name
        self._push = push
        self._lock = threading.Lock()
        self._form: Any = None
        self._hwnd = 0
        self._parent = 0

    @property
    def native(self) -> Any:
        """The host form, made on first use; None while the launcher shows no window."""
        with self._lock:
            if self._form is not None and _user32().IsWindow(self._parent) and _user32().IsWindow(self._hwnd):
                return self._form
            self._form, self._hwnd, self._parent = None, 0, 0
            parent = find_launcher_window(self._pid, self._class)
            if parent:
                self._start_form(parent)
            return self._form

    def _start_form(self, parent: int) -> None:
        import webview.platforms.winforms  # noqa: F401  pythonnet + WinForms + WebView2 assemblies
        from System.Threading import ApartmentState, Thread, ThreadStart

        ready = threading.Event()

        def run() -> None:
            try:
                from System.Drawing import Color
                from System.Windows.Forms import Application, Form, FormBorderStyle, FormStartPosition

                user32 = _user32()
                # The launcher is per-monitor v2; a child of another awareness is misplaced.
                user32.SetThreadDpiAwarenessContext(_DPI_PER_MONITOR_V2)
                form = Form()
                form.FormBorderStyle = getattr(FormBorderStyle, "None")
                form.ShowInTaskbar = False
                form.StartPosition = FormStartPosition.Manual
                form.BackColor = Color.FromArgb(255, 16, 16, 16)
                hwnd = int(form.Handle.ToInt64())
                user32.SetWindowLongPtrW(hwnd, _GWL_STYLE, child_style(user32.GetWindowLongPtrW(hwnd, _GWL_STYLE)))
                user32.SetParent(hwnd, parent)
                self._form, self._hwnd, self._parent = form, hwnd, parent
                self._place([])
                form.Show()
            except Exception:
                _log.exception("browser pane host did not start")
                self._form, self._hwnd, self._parent = None, 0, 0
                ready.set()
                return
            ready.set()
            Application.Run()

        thread = Thread(ThreadStart(run))
        thread.SetApartmentState(ApartmentState.STA)
        thread.IsBackground = True
        thread.Start()
        if not ready.wait(_START_WAIT_S):
            _log.warning("browser pane host took over %ss to start", _START_WAIT_S)

    def client_size(self) -> tuple[int, int]:
        """The launcher window's client area in pixels (the page's whole viewport)."""
        rc = wintypes.RECT()
        if self._parent:
            _user32().GetClientRect(self._parent, ctypes.byref(rc))
        return int(rc.right - rc.left), int(rc.bottom - rc.top)

    def after_bounds(self, controls: list[Any]) -> None:
        """UI thread, after a pane moved or showed/hid: refit and restack the form."""
        if self._form is not None:
            self._place(shown_rects(controls, self._form))

    def push_event(self, event: dict[str, Any]) -> None:
        push = self._push
        if push is None:
            from frontend.ui_web.agent_turns.push import push_ui_event

            push = push_ui_event
        push(event)

    def _place(self, rects: list[tuple[int, int, int, int]]) -> None:
        """Fill the launcher's client area; with panes on screen: on top, clipped to them;
        with none: the whole form under the page (an agent's behind pane keeps drawing)."""
        user32, gdi32 = _user32(), _gdi32()
        rgn = None
        if rects:
            rgn = gdi32.CreateRectRgn(0, 0, 0, 0)
            for x, y, w, h in rects:
                part = gdi32.CreateRectRgn(x, y, x + w, y + h)
                gdi32.CombineRgn(rgn, rgn, part, _RGN_OR)
                gdi32.DeleteObject(part)
        user32.SetWindowRgn(self._hwnd, rgn, True)  # the system owns rgn now; None = whole form
        w, h = self.client_size()
        user32.SetWindowPos(self._hwnd, _HWND_TOP if rects else _HWND_BOTTOM, 0, 0, w, h,
                            _SWP_NOACTIVATE | _SWP_SHOWWINDOW)
