"""Region capture via Win+Shift+S (clipboard), then attach to chat.

``ms-screenclip:`` opens the full Snipping Tool app (save-to-Screenshots).
We synthesize the Win+Shift+S hotkey instead — that overlay copies to the
clipboard, which we then pull into the composer.
"""

from __future__ import annotations

import base64
import ctypes
import io
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from typing import Any

_WAIT_TIMEOUT_S = 120.0
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_session_lock = threading.Lock()

# --- SendInput bits (64-bit safe) ---
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_LWIN = 0x5B
VK_SHIFT = 0x10
VK_S = 0x53


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = (
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    )


class _INPUTUNION(ctypes.Union):
    _fields_ = (("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT))


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = (("type", wintypes.DWORD), ("u", _INPUTUNION))


def snip_screen_interactive(tk_root: Any = None) -> dict[str, Any]:
    """Fire Win+Shift+S and return the accepted PNG from the clipboard."""
    del tk_root
    if sys.platform != "win32":
        return {"ok": False, "reason": "unsupported"}
    if not _session_lock.acquire(blocking=False):
        return {"ok": False, "reason": "busy"}
    try:
        return _run_win_shift_s()
    finally:
        _session_lock.release()


def _clear_clipboard() -> None:
    user32 = ctypes.windll.user32
    if not user32.OpenClipboard(None):
        return
    try:
        user32.EmptyClipboard()
    finally:
        user32.CloseClipboard()


def _key_input(vk: int, flags: int = 0) -> INPUT:
    return INPUT(
        type=INPUT_KEYBOARD,
        ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0),
    )


def invoke_win_shift_s() -> bool:
    """Synthesize Win+Shift+S. Returns True if SendInput accepted all events."""
    events = (
        _key_input(VK_LWIN),
        _key_input(VK_SHIFT),
        _key_input(VK_S),
        _key_input(VK_S, KEYEVENTF_KEYUP),
        _key_input(VK_SHIFT, KEYEVENTF_KEYUP),
        _key_input(VK_LWIN, KEYEVENTF_KEYUP),
    )
    arr = (INPUT * len(events))(*events)
    sent = int(ctypes.windll.user32.SendInput(len(events), arr, ctypes.sizeof(INPUT)))
    return sent == len(events)


def _clipper_running() -> bool:
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq ScreenClippingHost.exe", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception:
        return False
    return "screenclippinghost.exe" in (completed.stdout or "").lower()


def _png_result(image: Any) -> dict[str, Any]:
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return {
        "ok": True,
        "data_base64": base64.b64encode(buf.getvalue()).decode("ascii"),
        "mime": "image/png",
        "width": int(image.width),
        "height": int(image.height),
    }


def _grab_clipboard_image() -> Any | None:
    from PIL import Image, ImageGrab

    try:
        grabbed = ImageGrab.grabclipboard()
    except Exception:
        return None
    return grabbed if isinstance(grabbed, Image.Image) else None


def _run_win_shift_s() -> dict[str, Any]:
    try:
        _clear_clipboard()
    except Exception:
        pass

    if not invoke_win_shift_s():
        return {"ok": False, "reason": "error", "error": "SendInput Win+Shift+S failed"}

    deadline = time.monotonic() + _WAIT_TIMEOUT_S
    saw_host = False
    # Hotkey UI can take a moment to spawn the host process.
    start_grace = time.monotonic() + 6.0

    while time.monotonic() < deadline:
        time.sleep(0.12)
        img = _grab_clipboard_image()
        if img is not None:
            return _png_result(img)

        running = _clipper_running()
        if running:
            saw_host = True
            continue
        if saw_host:
            for _ in range(15):
                time.sleep(0.1)
                late = _grab_clipboard_image()
                if late is not None:
                    return _png_result(late)
            return {"ok": False, "reason": "cancelled"}
        if time.monotonic() >= start_grace:
            # Hotkey never started the clip overlay (policy / disabled).
            return {
                "ok": False,
                "reason": "error",
                "error": "Screen clip overlay did not start — is Win+Shift+S enabled?",
            }

    return {"ok": False, "reason": "cancelled"}
