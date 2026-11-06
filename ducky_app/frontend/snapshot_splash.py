"""Show the last session's picture of Ducky (frontend/boot_snapshot.py) while the app starts.

A plain Win32 popup on its own thread with its own message loop: it stays up while the main
thread is busy importing and then blocked in ``webview.start``, which a Tk splash can't. It is
topmost and never takes focus, sits exactly where the window opens, shows the "starting"
cursor, and closes when the page has painted (``/__panel_boot`` ``painted``), on a hand-off,
or after ``timeout_s`` whatever happens.
"""
from __future__ import annotations

import sys
import threading
from typing import Any

_state: dict[str, Any] = {"hwnd": 0, "thread": None}
_CLASS = "UEFNDuckySnapshotSplash"

WM_DESTROY = 0x0002
WM_PAINT = 0x000F
WM_CLOSE = 0x0010
WM_ERASEBKGND = 0x0014
WM_MOUSEACTIVATE = 0x0021
WM_TIMER = 0x0113
MA_NOACTIVATE = 3
WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
SW_SHOWNOACTIVATE = 4
IMAGE_BITMAP = 0
LR_LOADFROMFILE = 0x0010
LR_CREATEDIBSECTION = 0x2000
IDC_APPSTARTING = 32650
SRCCOPY = 0x00CC0020


def is_showing() -> bool:
    return bool(_state["hwnd"])


def show(snapshot: dict[str, Any], *, timeout_s: float = 15.0) -> bool:
    """Show ``snapshot`` (``{path, x, y, w, h}``); True once the window is up."""
    if sys.platform != "win32" or _state["thread"] is not None:
        return False
    ready = threading.Event()
    thread = threading.Thread(target=_run, args=(snapshot, timeout_s, ready), daemon=True,
                              name="snapshot-splash")
    _state["thread"] = thread
    thread.start()
    ready.wait(2.0)
    return is_showing()


def dismiss() -> None:
    hwnd = _state["hwnd"]
    if not hwnd or sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes as wt

    user32 = ctypes.WinDLL("user32")
    user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.PostMessageW(wt.HWND(hwnd), WM_CLOSE, 0, 0)


def _run(snapshot: dict[str, Any], timeout_s: float, ready: threading.Event) -> None:
    import ctypes
    from ctypes import wintypes as wt

    lresult = ctypes.c_ssize_t
    wndproc_t = ctypes.WINFUNCTYPE(lresult, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", wt.UINT), ("lpfnWndProc", wndproc_t), ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                    ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                    ("lpszClassName", wt.LPCWSTR)]

    class PAINTSTRUCT(ctypes.Structure):
        _fields_ = [("hdc", wt.HDC), ("fErase", wt.BOOL), ("rcPaint", wt.RECT), ("fRestore", wt.BOOL),
                    ("fIncUpdate", wt.BOOL), ("rgbReserved", ctypes.c_byte * 32)]

    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.LoadImageW.restype = wt.HANDLE
    user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT]
    user32.LoadCursorW.restype = wt.HANDLE
    user32.LoadCursorW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.CreateWindowExW.restype = wt.HWND
    user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
    user32.DefWindowProcW.restype = lresult
    user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.BeginPaint.restype = wt.HDC
    user32.BeginPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
    user32.EndPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
    user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
    user32.UpdateWindow.argtypes = [wt.HWND]
    user32.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p]
    user32.DestroyWindow.argtypes = [wt.HWND]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
    gdi32.CreateCompatibleDC.restype = wt.HDC
    gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
    gdi32.SelectObject.restype = wt.HGDIOBJ
    gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
    gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HDC,
                             ctypes.c_int, ctypes.c_int, wt.DWORD]
    gdi32.DeleteDC.argtypes = [wt.HDC]
    gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
    kernel32.GetModuleHandleW.restype = wt.HMODULE
    kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]

    hbmp = None
    try:
        # Physical pixels on this thread only (the snapshot was taken that way); the process'
        # DPI mode belongs to WinForms and is never touched.
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        x, y, w, h = (int(snapshot[k]) for k in ("x", "y", "w", "h"))
        hbmp = user32.LoadImageW(None, str(snapshot["path"]), IMAGE_BITMAP, 0, 0,
                                 LR_LOADFROMFILE | LR_CREATEDIBSECTION)
        if not hbmp:
            return

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_PAINT:
                ps = PAINTSTRUCT()
                hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
                mem = gdi32.CreateCompatibleDC(hdc)
                old = gdi32.SelectObject(mem, hbmp)
                gdi32.BitBlt(hdc, 0, 0, w, h, mem, 0, 0, SRCCOPY)
                gdi32.SelectObject(mem, old)
                gdi32.DeleteDC(mem)
                user32.EndPaint(hwnd, ctypes.byref(ps))
                return 0
            if msg == WM_ERASEBKGND:
                return 1
            if msg == WM_MOUSEACTIVATE:
                return MA_NOACTIVATE
            if msg in (WM_TIMER, WM_CLOSE):
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                _state["hwnd"] = 0
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        proc = wndproc_t(wndproc)
        _state["proc"] = proc  # the callback must outlive the window
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = proc
        wc.hInstance = hinst
        wc.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(IDC_APPSTARTING))
        wc.lpszClassName = _CLASS
        user32.RegisterClassW(ctypes.byref(wc))  # already registered: CreateWindowExW still finds it
        hwnd = user32.CreateWindowExW(WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE, _CLASS, "UEFN Ducky",
                                      WS_POPUP, x, y, w, h, None, None, hinst, None)
        if not hwnd:
            return
        _state["hwnd"] = int(hwnd)
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        user32.UpdateWindow(hwnd)
        user32.SetTimer(hwnd, 1, max(1000, int(timeout_s * 1000)), None)
        ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    except Exception:
        pass
    finally:
        _state["hwnd"] = 0
        ready.set()
        if hbmp:
            gdi32.DeleteObject(hbmp)
