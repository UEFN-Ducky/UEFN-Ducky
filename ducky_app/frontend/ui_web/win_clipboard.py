"""Windows Unicode clipboard write — WebView2 navigator.clipboard often no-ops.

On Linux the same calls go through wl-copy/wl-paste (Wayland) or xclip (X11), plus PNG;
on macOS through NSPasteboard, plus PNG."""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import time

IS_LINUX = sys.platform.startswith("linux")
IS_MAC = sys.platform == "darwin"


def _mac_pasteboard():
    """(AppKit, the general NSPasteboard), or (None, None) without pyobjc."""
    try:
        import AppKit  # type: ignore[import-not-found]
    except ImportError:
        return None, None
    return AppKit, AppKit.NSPasteboard.generalPasteboard()


def _mac_write(text: str | None = None, png: bytes = b"") -> bool:
    AppKit, pb = _mac_pasteboard()
    if pb is None:
        return False
    pb.clearContents()
    if text is not None:
        return bool(pb.setString_forType_(text, AppKit.NSPasteboardTypeString))
    from Foundation import NSData  # type: ignore[import-not-found]

    return bool(pb.setData_forType_(NSData.dataWithBytes_length_(png, len(png)), AppKit.NSPasteboardTypePNG))


def _mac_png() -> bytes:
    """The pasteboard's picture as PNG: macOS screenshots and most apps copy TIFF, converted here."""
    AppKit, pb = _mac_pasteboard()
    if pb is None:
        return b""
    data = pb.dataForType_(AppKit.NSPasteboardTypePNG)
    if data is None:
        tiff = pb.dataForType_(AppKit.NSPasteboardTypeTIFF)
        rep = AppKit.NSBitmapImageRep.imageRepWithData_(tiff) if tiff is not None else None
        data = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {}) if rep is not None else None
    return bytes(data) if data is not None else b""


def _linux_cmd(write: bool, mime: str) -> list[str] | None:
    """The clipboard tool for this session: wl-clipboard on Wayland, xclip on X11."""
    # ponytail: CLI tools, not Gtk.Clipboard: Gtk needs its main-loop thread, these work from any thread.
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        return ["wl-copy", "--type", mime] if write else ["wl-paste", "--no-newline", "--type", mime]
    if os.environ.get("DISPLAY") and shutil.which("xclip"):
        mime = "UTF8_STRING" if mime.startswith("text/plain") else mime  # the X11 text target apps offer
        return ["xclip", "-selection", "clipboard", "-t", mime] + (["-i"] if write else ["-o"])
    return None


def _linux_write(data: bytes, mime: str) -> bool:
    cmd = _linux_cmd(True, mime)
    if not cmd:
        return False
    try:
        # wl-copy and xclip fork a child that keeps serving the selection; stdout/stderr
        # must not be pipes or run() would wait on that child.
        return subprocess.run(cmd, input=data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _linux_read(mime: str) -> bytes:
    cmd = _linux_cmd(False, mime)
    if not cmd:
        return b""
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return b""
    return done.stdout if done.returncode == 0 else b""


def set_clipboard_png(png: bytes) -> bool:
    """Put a PNG on the clipboard (Linux and macOS; Windows images go through WebView2)."""
    if IS_MAC:
        return bool(png) and _mac_write(png=png)
    return bool(IS_LINUX and png) and _linux_write(png, "image/png")


def get_clipboard_png() -> bytes:
    """The clipboard's PNG, or b'' when it holds none (Linux and macOS)."""
    if not IS_LINUX and not IS_MAC:
        return b""
    data = _mac_png() if IS_MAC else _linux_read("image/png")
    return data if data.startswith(b"\x89PNG") else b""

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def _win() -> tuple[ctypes.WinDLL, ctypes.WinDLL]:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    return user32, kernel32


def set_clipboard_text(text: str) -> bool:
    if IS_MAC:
        return _mac_write(text=str(text or ""))
    if IS_LINUX:
        return _linux_write(str(text or "").encode("utf-8"), "text/plain;charset=utf-8")
    if sys.platform != "win32":
        return False
    payload = str(text or "").encode("utf-16-le") + b"\x00\x00"
    user32, kernel32 = _win()
    opened = False
    for _ in range(8):
        if user32.OpenClipboard(None):
            opened = True
            break
        time.sleep(0.05)
    if not opened:
        return False
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
        if not handle:
            return False
        locked = kernel32.GlobalLock(handle)
        if not locked:
            kernel32.GlobalFree(handle)
            return False
        ctypes.memmove(locked, payload, len(payload))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
        return True
    finally:
        user32.CloseClipboard()


def get_clipboard_text() -> str:
    """Read Unicode text from the clipboard. Empty when none or not Windows/Linux/macOS."""
    if IS_MAC:
        AppKit, pb = _mac_pasteboard()
        return str((pb.stringForType_(AppKit.NSPasteboardTypeString) if pb is not None else None) or "")
    if IS_LINUX:
        return _linux_read("text/plain;charset=utf-8").decode("utf-8", "replace")
    if sys.platform != "win32":
        return ""
    user32, kernel32 = _win()
    user32.GetClipboardData.argtypes = [ctypes.c_uint]
    user32.GetClipboardData.restype = ctypes.c_void_p
    opened = False
    for _ in range(8):
        if user32.OpenClipboard(None):
            opened = True
            break
        time.sleep(0.05)
    if not opened:
        return ""
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        locked = kernel32.GlobalLock(handle)
        if not locked:
            return ""
        try:
            return ctypes.wstring_at(locked)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()
