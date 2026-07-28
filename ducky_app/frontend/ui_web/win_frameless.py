"""Win32 helpers for frameless pywebview windows (BrowserForm WndProc + WebView2 edge bleed)."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

if sys.platform == "win32":
    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32
else:
    user32 = None
    shell32 = None

_AUMID_SET = False


def ensure_app_user_model_id(app_id: str = "UEFN.Ducky") -> None:
    """Group all HWNDs under one taskbar entry on Windows."""
    global _AUMID_SET
    if sys.platform != "win32" or _AUMID_SET:
        return
    try:
        shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        _AUMID_SET = True
    except Exception:
        pass


def _hwnd_from_pywebview(window: object) -> int | None:
    native = getattr(window, "native", None)
    if native is None or not hasattr(native, "Handle"):
        return None
    try:
        return int(native.Handle.ToInt32())
    except Exception:
        return None


def set_window_icon_hwnd(window: object, *, mode: str = "online") -> bool:
    """Give the window a real HICON so the taskbar, Alt-Tab, and **Task Manager** show the app icon.

    pywebview/EdgeChromium never sets a window icon, so those surfaces fall back to a generic icon
    even though the .exe file icon is correct. We load the PNG for ``mode``, write a temp .ico, then
    push it via WM_SETICON (title bar / Alt-Tab) and the window class (taskbar / Task Manager).
    """
    if sys.platform != "win32":
        return False
    hwnd = _hwnd_from_pywebview(window)
    if not hwnd:
        return False
    try:
        from frontend.tray_icon import resolve_app_icon_path

        ico_path = resolve_app_icon_path(mode)  # type: ignore[arg-type]
        if not ico_path or not ico_path.is_file():
            return False

        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        WM_SETICON = 0x0080
        ICON_SMALL, ICON_BIG = 0, 1
        GCLP_HICON, GCLP_HICONSM = -14, -34

        user32.LoadImageW.restype = ctypes.c_void_p
        user32.LoadImageW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]
        big = user32.LoadImageW(None, str(ico_path), IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
        small = user32.LoadImageW(None, str(ico_path), IMAGE_ICON, 16, 16, LR_LOADFROMFILE)

        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        user32.SendMessageW.restype = ctypes.c_void_p
        if big:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big)
        if small:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small)

        set_class = getattr(user32, "SetClassLongPtrW", None) or user32.SetClassLongW
        set_class.restype = ctypes.c_void_p
        set_class.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        if big:
            set_class(hwnd, GCLP_HICON, big)
        if small:
            set_class(hwnd, GCLP_HICONSM, small)
        return True
    except Exception:
        return False


def get_window_scale(window: object) -> float:
    """Logical-to-physical scale for the pywebview window (1.0 on 100% DPI)."""
    if sys.platform != "win32":
        return 1.0
    native = getattr(window, "native", None)
    if native is None:
        return 1.0
    try:
        return float(getattr(native, "_scale", 1.0) or 1.0)
    except (TypeError, ValueError):
        return 1.0


def set_window_bounds_hwnd(window: object, x: int, y: int, width: int, height: int) -> bool:
    """Apply position + size in one SetWindowPos call (fixes left/top resize)."""
    if sys.platform != "win32":
        return False
    hwnd = _hwnd_from_pywebview(window)
    if not hwnd:
        return False
    scale = get_window_scale(window)
    SWP_NOZORDER = 0x0004
    SWP_NOACTIVATE = 0x0010
    user32.SetWindowPos(
        hwnd,
        None,
        int(x * scale),
        int(y * scale),
        int(width * scale),
        int(height * scale),
        SWP_NOZORDER | SWP_NOACTIVATE,
    )
    return True


def force_window_foreground(window: object) -> bool:
    """Raise a pywebview window above other applications."""
    if sys.platform != "win32":
        return False
    hwnd = _hwnd_from_pywebview(window)
    if not hwnd:
        return False
    try:
        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


# Win32 style / hit-test constants for native Aero Snap drag and resize.
_GWL_STYLE = -16
_GWL_EXSTYLE = -20
_WS_CAPTION = 0x00C00000
_WS_THICKFRAME = 0x00040000
_WS_BORDER = 0x00800000
_WS_DLGFRAME = 0x00400000
_WS_MAXIMIZEBOX = 0x00010000
_WS_MINIMIZEBOX = 0x00020000
_WS_SYSMENU = 0x00080000
_WS_EX_WINDOWEDGE = 0x00000100
_WS_EX_CLIENTEDGE = 0x00000200
_SWP_FRAMECHANGED = 0x0020
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004
_WM_NCLBUTTONDOWN = 0x00A1
_WM_NCCALCSIZE = 0x0083
_WM_NCHITTEST = 0x0084
_WM_NCACTIVATE = 0x0086
_WM_GETMINMAXINFO = 0x0024
_WM_SYSCOMMAND = 0x0112
_SC_SIZE = 0xF000
_HTCAPTION = 2
_HTCLIENT = 1
_HTLEFT = 10
_HTRIGHT = 11
_HTTOP = 12
_HTTOPLEFT = 13
_HTTOPRIGHT = 14
_DWMWA_NCRENDERING_POLICY = 2
_DWMNCRP_DISABLED = 2
_DWMWA_BORDER_COLOR = 34
_DWMWA_CAPTION_COLOR = 35
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_ROUND = 2
_DWMWA_COLOR_NONE = 0xFFFFFFFE
_APP_BG_COLORREF = 0x000A0A0A  # #0a0a0a in Win32 BGR
_HTBOTTOM = 15
_HTBOTTOMLEFT = 16
_HTBOTTOMRIGHT = 17
# SC_SIZE | WMSZ_* edge codes (1..8). Not HT* (10..17).
_RESIZE_EDGE_HT: dict[str, int] = {
    "w": 1,   # WMSZ_LEFT
    "e": 2,   # WMSZ_RIGHT
    "n": 3,   # WMSZ_TOP
    "nw": 4,  # WMSZ_TOPLEFT
    "ne": 5,  # WMSZ_TOPRIGHT
    "s": 6,   # WMSZ_BOTTOM
    "sw": 7,  # WMSZ_BOTTOMLEFT
    "se": 8,  # WMSZ_BOTTOMRIGHT
}


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MINMAXINFO(ctypes.Structure):
    _fields_ = [
        ("ptReserved", _POINT),
        ("ptMaxSize", _POINT),
        ("ptMaxPosition", _POINT),
        ("ptMinTrackSize", _POINT),
        ("ptMaxTrackSize", _POINT),
    ]


def _hwnd_scale(hwnd: int) -> float:
    try:
        get_dpi = getattr(user32, "GetDpiForWindow", None)
        if get_dpi:
            return max(float(get_dpi(hwnd)) / 96.0, 1.0)
    except Exception:
        pass
    return 1.0


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [
        ("rgrc", _RECT * 3),
        ("lppos", ctypes.c_void_p),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", ctypes.c_ulong),
    ]


_window_min_track: dict[int, tuple[int, int]] = {}
_forced_min_track: tuple[int, int] | None = None
_pinned_webview_hwnds: set[int] = set()
_OriginalBrowserForm = None

_WM_SIZING = 0x0214
_WMSZ_LEFT = 1
_WMSZ_RIGHT = 2
_WMSZ_TOP = 3
_WMSZ_TOPLEFT = 4
_WMSZ_TOPRIGHT = 5
_WMSZ_BOTTOM = 6
_WMSZ_BOTTOMLEFT = 7
_WMSZ_BOTTOMRIGHT = 8


def _current_min_track(hwnd: int | None) -> tuple[int, int] | None:
    if hwnd is not None:
        tracked = _window_min_track.get(hwnd)
        if tracked:
            return tracked
    return _forced_min_track


def _apply_winforms_minimum_size(window: object, min_width: int, min_height: int) -> None:
    """Keep WinForms MinimumSize in sync so native resize honors our track size."""
    try:
        native = getattr(window, "native", None)
        if native is None:
            return
        from System.Drawing import Size

        native.MinimumSize = Size(max(1, int(min_width)), max(1, int(min_height)))
        native.MaximumSize = Size(0, 0)
    except Exception:
        pass


def _clamp_sizing_rect(edge: int, rect: _RECT, min_w: int, min_h: int) -> None:
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width < min_w:
        if edge in (_WMSZ_LEFT, _WMSZ_TOPLEFT, _WMSZ_BOTTOMLEFT):
            rect.left = rect.right - min_w
        else:
            rect.right = rect.left + min_w
    if height < min_h:
        if edge in (_WMSZ_TOP, _WMSZ_TOPLEFT, _WMSZ_TOPRIGHT):
            rect.top = rect.bottom - min_h
        else:
            rect.bottom = rect.top + min_h


def _apply_min_track_impl(window: object, min_width: int, min_height: int) -> bool:
    global _forced_min_track
    w = max(1, int(min_width))
    h = max(1, int(min_height))
    _forced_min_track = (w, h)

    hwnd = _hwnd_from_pywebview(window)
    if not hwnd:
        refresh_window_chrome(window)
        hwnd = _hwnd_from_pywebview(window)
    if hwnd:
        _install_native_chrome_subclass(hwnd)
        _window_min_track[hwnd] = (w, h)
        _force_frame_changed(hwnd)

    try:
        window.min_size = (w, h)
    except Exception:
        pass
    _apply_winforms_minimum_size(window, w, h)
    return hwnd is not None


def set_window_min_track_size(window: object, min_width: int, min_height: int) -> bool:
    """Lower/raise the Win32 minimum resize track size (logical px)."""
    if sys.platform != "win32":
        return False
    native = getattr(window, "native", None)
    state = {"ok": False}

    def apply() -> None:
        state["ok"] = _apply_min_track_impl(window, min_width, min_height)

    if native is not None:
        _run_on_form_ui(native, apply)
    else:
        apply()
    return bool(state["ok"])


def clear_window_min_track_size(window: object) -> bool:
    if sys.platform != "win32":
        return False
    from frontend.ui_web.window_layout import MAIN_MIN_HEIGHT, MAIN_MIN_WIDTH

    global _forced_min_track
    _forced_min_track = None
    native = getattr(window, "native", None)
    state = {"ok": False}

    def apply() -> None:
        hwnd = _hwnd_from_pywebview(window)
        if hwnd:
            release_native_chrome_subclass(hwnd)
        try:
            window.min_size = (MAIN_MIN_WIDTH, MAIN_MIN_HEIGHT)
        except Exception:
            pass
        _apply_winforms_minimum_size(window, MAIN_MIN_WIDTH, MAIN_MIN_HEIGHT)
        state["ok"] = True

    if native is not None:
        _run_on_form_ui(native, apply)
    else:
        apply()
    return bool(state["ok"])


def read_window_bounds(window: object) -> dict[str, int | float]:
    scale = get_window_scale(window)
    return {
        "x": int(getattr(window, "x", 0) or 0),
        "y": int(getattr(window, "y", 0) or 0),
        "width": int(getattr(window, "width", 0) or 0),
        "height": int(getattr(window, "height", 0) or 0),
        "scale": scale,
    }


def enter_sidebar_only_layout(window: object) -> dict[str, int | float] | None:
    """Save bounds and apply sidebar-only min track on the WinForms UI thread."""
    from frontend.ui_web.window_layout import SIDEBAR_ONLY_MIN_WIDTH

    native = getattr(window, "native", None)
    state: dict[str, object] = {"bounds": None}

    def apply() -> None:
        state["bounds"] = read_window_bounds(window)
        _apply_min_track_impl(window, SIDEBAR_ONLY_MIN_WIDTH, SIDEBAR_ONLY_MIN_WIDTH)

    if native is not None:
        _run_on_form_ui(native, apply)
    else:
        apply()
    bounds = state.get("bounds")
    return bounds if isinstance(bounds, dict) else None


def exit_sidebar_only_layout(window: object) -> None:
    clear_window_min_track_size(window)


def _resize_border_px(hwnd: int) -> int:
    """Physical pixels of the OS resize band (all edges)."""
    try:
        SM_CYSIZEFRAME = 33
        SM_CXPADDEDBORDER = 92
        frame = int(user32.GetSystemMetrics(SM_CYSIZEFRAME))
        pad = int(user32.GetSystemMetrics(SM_CXPADDEDBORDER))
        return max(frame + pad, 1)
    except Exception:
        return 8


def _top_resize_border_px(hwnd: int) -> int:
    return _resize_border_px(hwnd)


def _is_maximized(hwnd: int) -> bool:
    try:
        return bool(user32.IsZoomed(hwnd))
    except Exception:
        return False


def is_window_maximized(window: object) -> bool:
    """Real OS zoom state for the window (authoritative — reflects native double-click maximise)."""
    if sys.platform != "win32":
        return False
    hwnd = _hwnd_from_pywebview(window)
    if not hwnd:
        return False
    return _is_maximized(hwnd)


def _force_frame_changed(hwnd: int) -> None:
    try:
        user32.SetWindowPos(
            hwnd,
            None,
            0,
            0,
            0,
            0,
            _SWP_FRAMECHANGED | _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER,
        )
    except Exception:
        pass


def _set_nccalcsize_maximized_work_area(params: _NCCALCSIZE_PARAMS, hwnd: int) -> bool:
    """Maximized client = monitor work area (WebView2 #2549)."""
    try:
        MONITOR_DEFAULTTONULL = 0
        user32.MonitorFromRect.restype = wintypes.HMONITOR
        user32.MonitorFromRect.argtypes = [ctypes.POINTER(_RECT), ctypes.c_uint]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO)]
        hmon = user32.MonitorFromRect(ctypes.byref(params.rgrc[0]), MONITOR_DEFAULTTONULL)
        if not hmon:
            return False
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            return False
        params.rgrc[0] = mi.rcWork
        return True
    except Exception:
        return False


def _run_on_form_ui(native: object, fn) -> None:
    try:
        if bool(getattr(native, "InvokeRequired", False)):
            from System import Action

            native.Invoke(Action(fn))
            return
    except Exception:
        pass
    try:
        fn()
    except Exception:
        pass


def reset_webview_pin(window: object) -> None:
    hwnd = _hwnd_from_pywebview(window)
    if hwnd:
        _pinned_webview_hwnds.discard(hwnd)
    _pin_webview_once(window)


def _pin_webview_once(window: object) -> None:
    """One-shot: align WebView2 to (0,0). Never call during resize — Dock.Fill handles that."""
    hwnd = _hwnd_from_pywebview(window)
    if not hwnd or hwnd in _pinned_webview_hwnds:
        return
    native = getattr(window, "native", None)
    if native is None:
        return

    def _layout() -> None:
        h = _hwnd_from_pywebview(window)
        if not h or h in _pinned_webview_hwnds:
            return
        try:
            import System.Windows.Forms as WinForms

            webview_ctrl = getattr(native, "webview", None)
            if webview_ctrl is None:
                return
            # Zero padding + Dock.Fill is all that is needed now that the native subclass
            # gives a correct full-window client rect. Do NOT hand-place WebView2's child
            # HWNDs — that desyncs its hit-tester (app-region drag regions break after a
            # maximize→restore, making the whole window draggable).
            native.Padding = WinForms.Padding(0)
            webview_ctrl.Margin = WinForms.Padding(0)
            webview_ctrl.Dock = WinForms.DockStyle.Fill
            # Match app chrome so any sub-pixel edge never flashes the WinForms default beige.
            try:
                from System.Drawing import Color

                # COLORREF is 0x00BBGGRR
                bg = Color.FromArgb(
                    _APP_BG_COLORREF & 0xFF,
                    (_APP_BG_COLORREF >> 8) & 0xFF,
                    (_APP_BG_COLORREF >> 16) & 0xFF,
                )
                native.BackColor = bg
                webview_ctrl.BackColor = bg
            except Exception:
                pass
            _pinned_webview_hwnds.add(h)
        except Exception:
            pass

    _run_on_form_ui(native, _layout)


def refresh_window_chrome(window: object, *, pin: bool = False) -> bool:
    """Apply Win32 chrome styles. Optional one-shot WebView pin after first paint."""
    if sys.platform != "win32":
        return False
    hwnd = _hwnd_from_pywebview(window)
    if hwnd:
        _install_native_chrome_subclass(hwnd)  # idempotent; covers HandleCreated timing
    ok = ensure_snap_friendly_styles(window)
    if pin:
        _pin_webview_once(window)
    return ok


def _extend_frame_into_client(hwnd: int) -> None:
    """Hide native non-client chrome while keeping snap/resize behavior."""
    try:
        dwmapi = ctypes.windll.dwmapi

        class _MARGINS(ctypes.Structure):
            _fields_ = [
                ("cxLeftWidth", ctypes.c_int),
                ("cxRightWidth", ctypes.c_int),
                ("cyTopHeight", ctypes.c_int),
                ("cyBottomHeight", ctypes.c_int),
            ]

        # Zero margins — extended frame without reserving a painted NC band.
        m = _MARGINS(0, 0, 0, 0)
        dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(m))

        policy = ctypes.c_int(_DWMNCRP_DISABLED)
        dwmapi.DwmSetWindowAttribute(
            hwnd,
            _DWMWA_NCRENDERING_POLICY,
            ctypes.byref(policy),
            ctypes.sizeof(policy),
        )

        border = ctypes.c_int(_DWMWA_COLOR_NONE)
        for attr in (_DWMWA_BORDER_COLOR, _DWMWA_CAPTION_COLOR):
            try:
                dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(border), ctypes.sizeof(border))
            except Exception:
                pass

        corner = ctypes.c_int(_DWMWCP_ROUND)
        for attr in (_DWMWA_WINDOW_CORNER_PREFERENCE, 38):
            try:
                dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    attr,
                    ctypes.byref(corner),
                    ctypes.sizeof(corner),
                )
            except Exception:
                pass
        _force_frame_changed(hwnd)
    except Exception:
        pass


def ensure_snap_friendly_styles(window: object) -> bool:
    """Keep WS_CAPTION|WS_SYSMENU|WS_THICKFRAME (Electron hiddenInset).

    The caption is required for the taskbar minimize/restore *animation* (VS Code keeps it
    too); it is never visible because our WM_NCCALCSIZE subclass strips only the top band.
    Stripping WS_BORDER/WS_DLGFRAME here would clear WS_CAPTION and kill that animation,
    so we only strip the cosmetic extended-edge styles.
    """
    if sys.platform != "win32":
        return False
    hwnd = _hwnd_from_pywebview(window)
    if not hwnd:
        return False
    try:
        get_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
        set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        get_long.restype = ctypes.c_longlong
        get_long.argtypes = [wintypes.HWND, ctypes.c_int]
        set_long.restype = ctypes.c_longlong
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_longlong]

        style = int(get_long(hwnd, _GWL_STYLE))
        ex_style = int(get_long(hwnd, _GWL_EXSTYLE))
        strip_ex = _WS_EX_WINDOWEDGE | _WS_EX_CLIENTEDGE
        needed = (
            _WS_CAPTION | _WS_SYSMENU | _WS_THICKFRAME | _WS_MAXIMIZEBOX | _WS_MINIMIZEBOX
        )
        next_style = style | needed
        next_ex = ex_style & ~strip_ex
        changed = False
        if next_style != style:
            set_long(hwnd, _GWL_STYLE, next_style)
            changed = True
        if next_ex != ex_style:
            set_long(hwnd, _GWL_EXSTYLE, next_ex)
            changed = True
        if changed:
            _force_frame_changed(hwnd)
        _extend_frame_into_client(hwnd)
        return True
    except Exception:
        return False


def begin_native_window_move(
    window: object,
    screen_x: int | None = None,
    screen_y: int | None = None,
) -> bool:
    """Hand off window move to the OS so Aero Snap / edge alignment work."""
    if sys.platform != "win32":
        return False
    hwnd = _hwnd_from_pywebview(window)
    if not hwnd:
        return False
    try:
        if screen_x is None or screen_y is None:
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            screen_x = int(pt.x)
            screen_y = int(pt.y)
        lparam = (int(screen_y) << 16) | (int(screen_x) & 0xFFFF)
        user32.ReleaseCapture()
        user32.SendMessageW(hwnd, _WM_NCLBUTTONDOWN, _HTCAPTION, lparam)
        return True
    except Exception:
        return False


def begin_native_window_resize(window: object, edge: str) -> bool:
    """Hand off window resize to the OS (edge: n/s/e/w/nw/ne/sw/se).

    Prefer the OS L/R/B NC frame (see _chrome_subclass_proc). This SC_SIZE path
    is for edges with no frame (top) if a caller needs it; must run on the UI
    thread while LMB is still down.
    """
    if sys.platform != "win32":
        return False
    wmsz = _RESIZE_EDGE_HT.get((edge or "").lower())
    if wmsz is None:
        return False
    hwnd = _hwnd_from_pywebview(window)
    if not hwnd:
        return False
    try:
        user32.ReleaseCapture()
        user32.SendMessageW(hwnd, _WM_SYSCOMMAND, _SC_SIZE | wmsz, 0)
        return True
    except Exception:
        return False


# --- OS-level window subclass (bypasses pythonnet, which drops a sub-subclass WndProc) ---
# LONG_PTR/UINT_PTR-sized ctypes so 64-bit WPARAM/LPARAM/LRESULT round-trip correctly.
if sys.platform == "win32":
    _LRESULT = ctypes.c_ssize_t
    _WPARAM = ctypes.c_size_t
    _LPARAM = ctypes.c_ssize_t
    _WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, ctypes.c_uint, _WPARAM, _LPARAM)
    _GWLP_WNDPROC = -4
    # Dedicated WinDLL instance so setting argtypes never mutates the shared ctypes.windll cache.
    _user32_sc = ctypes.WinDLL("user32", use_last_error=True)
    _SetWindowLongPtr = getattr(_user32_sc, "SetWindowLongPtrW", None) or _user32_sc.SetWindowLongW
    _SetWindowLongPtr.restype = ctypes.c_void_p
    _SetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    _user32_sc.CallWindowProcW.restype = _LRESULT
    _user32_sc.CallWindowProcW.argtypes = [
        ctypes.c_void_p, wintypes.HWND, ctypes.c_uint, _WPARAM, _LPARAM,
    ]
    _user32_sc.DefWindowProcW.restype = _LRESULT
    _user32_sc.DefWindowProcW.argtypes = [wintypes.HWND, ctypes.c_uint, _WPARAM, _LPARAM]
_native_subclass: dict[int, object] = {}  # hwnd -> WNDPROC callback (kept alive)
_native_subclass_orig: dict[int, int] = {}  # hwnd -> original window proc pointer


def _chrome_subclass_proc(hwnd, msg, wparam, lparam):
    """Native window proc: strip the top caption, let the OS own the resize frame.

    Keep the standard L/R/B resize frame (DefWindowProc's inset) so the WebView2 child
    does NOT cover it — the OS handles side/bottom/corner resize natively (real cursor,
    Aero Snap, lockstep). Only the top caption is stripped (client top = window top) so
    there is no title bar and no native min/max/close buttons. The top edge has no OS
    resize frame, so a thin JS grip (WindowResize) handles top-edge resize only.
    """
    orig = _native_subclass_orig.get(hwnd, 0)
    try:
        if msg == _WM_NCCALCSIZE and wparam != 0:
            params = _NCCALCSIZE_PARAMS.from_address(lparam)
            if _is_maximized(hwnd):
                if _set_nccalcsize_maximized_work_area(params, hwnd):
                    return 0
            else:
                top = params.rgrc[0].top
                _user32_sc.CallWindowProcW(orig, hwnd, msg, wparam, lparam)
                params = _NCCALCSIZE_PARAMS.from_address(lparam)
                params.rgrc[0].top = top
                return 0
        elif msg == _WM_GETMINMAXINFO:
            if orig:
                result = _user32_sc.CallWindowProcW(orig, hwnd, msg, wparam, lparam)
            else:
                result = _user32_sc.DefWindowProcW(hwnd, msg, wparam, lparam)
            mins = _current_min_track(hwnd)
            if mins:
                info = _MINMAXINFO.from_address(lparam)
                info.ptMinTrackSize.x = int(mins[0])
                info.ptMinTrackSize.y = int(mins[1])
            return result
        elif msg == _WM_SIZING:
            mins = _current_min_track(hwnd)
            if mins:
                rect = ctypes.cast(lparam, ctypes.POINTER(_RECT)).contents
                _clamp_sizing_rect(int(wparam), rect, mins[0], mins[1])
                return 1
        elif msg == _WM_NCACTIVATE:
            if orig:
                return _user32_sc.CallWindowProcW(orig, hwnd, msg, wparam, -1)
    except Exception:
        pass
    if orig:
        return _user32_sc.CallWindowProcW(orig, hwnd, msg, wparam, lparam)
    return _user32_sc.DefWindowProcW(hwnd, msg, wparam, lparam)


def _install_native_chrome_subclass(hwnd: int) -> bool:
    """Replace the window proc with ours (chaining to the original). Idempotent per hwnd."""
    if sys.platform != "win32" or not hwnd:
        return False
    if hwnd in _native_subclass:
        return True
    try:
        cb = _WNDPROC(_chrome_subclass_proc)
        orig = _SetWindowLongPtr(hwnd, _GWLP_WNDPROC, ctypes.cast(cb, ctypes.c_void_p))
        _native_subclass[hwnd] = cb  # keep the callback alive (GC would crash the proc)
        _native_subclass_orig[hwnd] = int(orig) if orig else 0
        _force_frame_changed(hwnd)  # trigger a fresh WM_NCCALCSIZE through our proc now
        return True
    except Exception:
        return False


def release_native_chrome_subclass(hwnd: int) -> None:
    """Restore the original WNDPROC and drop kept-alive callback refs for a closed HWND."""
    if sys.platform != "win32" or not hwnd:
        return
    cb = _native_subclass.pop(hwnd, None)
    orig = _native_subclass_orig.pop(hwnd, None)
    _window_min_track.pop(hwnd, None)
    if orig and cb is not None:
        try:
            _SetWindowLongPtr(hwnd, _GWLP_WNDPROC, orig)
        except Exception:
            pass


def _make_chrome_browser_form():
    """ChromeBrowserForm hooks (HandleCreated/on_resize); NC math is a native HWND subclass."""
    from webview.platforms import winforms

    global _OriginalBrowserForm
    OriginalBrowserForm = winforms.BrowserView.BrowserForm
    _OriginalBrowserForm = OriginalBrowserForm
    original_on_resize = OriginalBrowserForm.on_resize

    class ChromeBrowserForm(OriginalBrowserForm):
        def __init__(self, window, cache_dir):
            OriginalBrowserForm.__init__(self, window, cache_dir)
            self._chrome_state = None
            self.HandleCreated += self._chrome_handle_created

        def _chrome_handle_created(self, _sender, _args) -> None:
            if not getattr(self, "frameless", False):
                return
            try:
                hwnd = _hwnd_from_pywebview(self.pywebview_window)
                if hwnd:
                    _install_native_chrome_subclass(hwnd)
                ensure_snap_friendly_styles(self.pywebview_window)
                reset_webview_pin(self.pywebview_window)
                _pin_webview_once(self.pywebview_window)
            except Exception:
                pass

        def on_resize(self, sender, args):
            import System.Windows.Forms as WinForms

            prev = getattr(self, "_chrome_state", None)
            original_on_resize(self, sender, args)
            if getattr(self, "frameless", False):
                cur = self.WindowState
                if cur != prev and (
                    cur == WinForms.FormWindowState.Maximized
                    or prev == WinForms.FormWindowState.Maximized
                ):
                    try:
                        h = _hwnd_from_pywebview(self.pywebview_window)
                        if h:
                            # Re-trigger WM_NCCALCSIZE after maximize/restore.
                            _force_frame_changed(h)
                        reset_webview_pin(self.pywebview_window)
                        _pin_webview_once(self.pywebview_window)
                    except Exception:
                        pass
                self._chrome_state = cur

    return ChromeBrowserForm


def install_pywebview_chrome_patches() -> None:
    """Patch pywebview BrowserForm + WebView2 for edge-to-edge frameless chrome."""
    if sys.platform != "win32":
        return
    if getattr(install_pywebview_chrome_patches, "_installed", False):
        return
    try:
        from webview.platforms import edgechromium, winforms

        ChromeBrowserForm = _make_chrome_browser_form()
        winforms.BrowserView.BrowserForm = ChromeBrowserForm

        original_ready = edgechromium.EdgeChrome.on_webview_ready
        original_nav = edgechromium.EdgeChrome.on_navigation_completed

        def on_webview_ready(self, sender, args):
            if args.IsSuccess:
                try:
                    sender.CoreWebView2.Settings.IsNonClientRegionSupportEnabled = True
                except Exception:
                    pass
                # Auto-allow mic for the local panel origin. App UI owns Allow/Block;
                # this only silences WebView2's chrome prompt and persists the grant.
                try:
                    core = sender.CoreWebView2

                    def _on_permission_requested(_s, e):
                        try:
                            # CoreWebView2PermissionKind: Unknown=0, Microphone=1, Camera=2
                            kind = e.PermissionKind
                            try:
                                kind_i = int(kind)
                            except Exception:
                                kind_i = int(getattr(kind, "value", -1))
                            if kind_i != 1 and "Microphone" not in str(kind):
                                return
                            # Prefer typed enum when pythonnet exposes it.
                            try:
                                from Microsoft.Web.WebView2.Core import (  # type: ignore
                                    CoreWebView2PermissionState,
                                )

                                e.State = CoreWebView2PermissionState.Allow
                            except Exception:
                                e.State = 1  # Allow
                            for attr, val in (("Handled", True), ("SavesInProfile", True)):
                                if hasattr(e, attr):
                                    try:
                                        setattr(e, attr, val)
                                    except Exception:
                                        pass
                        except Exception:
                            pass

                    core.PermissionRequested += _on_permission_requested
                except Exception:
                    pass
            original_ready(self, sender, args)

        def on_navigation_completed(self, sender, args):
            original_nav(self, sender, args)
            try:
                _pin_webview_once(self.pywebview_window)
            except Exception:
                pass

        edgechromium.EdgeChrome.on_webview_ready = on_webview_ready
        edgechromium.EdgeChrome.on_navigation_completed = on_navigation_completed

        original_theme = _OriginalBrowserForm.update_title_bar_theme

        def update_title_bar_theme(self, *_args, **_kwargs):
            original_theme(self)
            if getattr(self, "frameless", False):
                hwnd = _hwnd_from_pywebview(self.pywebview_window)
                if hwnd:
                    _extend_frame_into_client(hwnd)
                    _force_frame_changed(hwnd)

        ChromeBrowserForm.update_title_bar_theme = update_title_bar_theme
        install_pywebview_chrome_patches._installed = True  # type: ignore[attr-defined]
    except Exception:
        pass


def install_chrome_refresh_hooks() -> None:
    """Backward-compatible alias — use install_pywebview_chrome_patches."""
    install_pywebview_chrome_patches()


def install_sync_drag_bridge() -> None:
    """Handle native drag on the UI thread during WebView2 mousedown (must be sync)."""
    if sys.platform != "win32":
        return
    try:
        import webview.util as webview_util

        if getattr(install_sync_drag_bridge, "_installed", False):
            return
        original = webview_util.js_bridge_call

        def patched(window: object, func_name: str, param: object, value_id: str) -> None:
            if func_name == "uefnNativeWindowMove":
                sx = sy = None
                if isinstance(param, (list, tuple)) and len(param) >= 2:
                    try:
                        sx, sy = int(param[0]), int(param[1])
                    except (TypeError, ValueError):
                        pass
                begin_native_window_move(window, sx, sy)
                return
            original(window, func_name, param, value_id)

        webview_util.js_bridge_call = patched
        install_sync_drag_bridge._installed = True  # type: ignore[attr-defined]
    except Exception:
        pass
