"""Region capture via Win+Shift+S (clipboard), then attach to chat.

``ms-screenclip:`` opens the full Snipping Tool app (save-to-Screenshots).
We synthesize the Win+Shift+S hotkey instead — that overlay copies to the
clipboard, which we then pull into the composer.

Linux: X11 grabs the screen with ``mss`` and lets the user drag a box on a
full-screen override-redirect overlay showing that frozen grab. Wayland asks
xdg-desktop-portal's Screenshot with ``interactive=true`` (the desktop's own picker).
"""

from __future__ import annotations

import base64
import ctypes
import io
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from typing import Any

from frontend import platform_info

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
    if platform_info.IS_LINUX:
        if not _session_lock.acquire(blocking=False):
            return {"ok": False, "reason": "busy"}
        try:
            return _snip_linux()
        finally:
            _session_lock.release()
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
            text=True, errors="replace",
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


# --- Linux ---------------------------------------------------------------

_CANCELLED = {"ok": False, "reason": "cancelled"}


def _snip_linux() -> dict[str, Any]:
    kind = platform_info.session_type()
    try:
        if kind == "wayland":
            return _portal_snip()
        if kind == "x11":
            return _x11_snip()
    except Exception as exc:
        return {"ok": False, "reason": "error", "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": False, "reason": "unsupported", "error": "no X11 or Wayland session"}


def _norm_rect(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """Two drag corners -> PIL crop box (left, top, right, bottom); None when under 2 px."""
    left, right = sorted((int(a[0]), int(b[0])))
    top, bottom = sorted((int(a[1]), int(b[1])))
    if right - left < 2 or bottom - top < 2:
        return None
    return left, top, right, bottom


def _x11_snip(timeout_s: float = _WAIT_TIMEOUT_S) -> dict[str, Any]:
    import mss
    from PIL import Image

    with (getattr(mss, "MSS", None) or mss.mss)() as sct:  # MSS on mss 10+, mss() before
        mon = dict(sct.monitors[0])  # the whole virtual screen
        shot = sct.grab(mon)
    full = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    box = _x11_pick_rect(mon, shot.bgra, timeout_s)
    if box is None:
        return dict(_CANCELLED)
    return _png_result(full.crop(box))


def _x11_pick_rect(mon: dict[str, int], bgra: bytes, timeout_s: float) -> tuple[int, int, int, int] | None:
    """Show the frozen grab full-screen, let the user drag a box. Esc / right-click / timeout cancel."""
    import select

    from Xlib import XK, X
    from Xlib import display as xdisplay

    d = xdisplay.Display()
    win = None
    try:
        screen = d.screen()
        root = screen.root
        w, h = int(mon["width"]), int(mon["height"])
        depth = screen.root_depth
        font = d.open_font("cursor")
        cursor = font.create_glyph_cursor(font, 34, 35, (65535, 65535, 65535), (0, 0, 0))  # XC_crosshair
        attrs: dict[str, Any] = {
            "override_redirect": True,
            "background_pixel": screen.black_pixel,
            "cursor": cursor,
            "event_mask": X.ExposureMask | X.ButtonPressMask | X.ButtonReleaseMask
            | X.PointerMotionMask | X.KeyPressMask | X.StructureNotifyMask,
        }
        # ponytail: the grab is painted only on 24/32-bit visuals (BGRX bytes); other depths get a black overlay.
        if depth in (24, 32):
            pix = root.create_pixmap(w, h, depth)
            pgc = pix.create_gc()
            stride = w * 4
            rows = max(1, 200_000 // stride)  # stay under the core request size limit
            for y in range(0, h, rows):
                n = min(rows, h - y)
                pix.put_image(pgc, 0, y, w, n, X.ZPixmap, depth, 0, bgra[y * stride:(y + n) * stride])
            pgc.free()
            attrs["background_pixmap"] = pix
            del attrs["background_pixel"]
        win = root.create_window(
            int(mon["left"]), int(mon["top"]), w, h, 0, depth, X.InputOutput, X.CopyFromParent, **attrs
        )
        win.set_wm_name("UEFN Ducky snip")
        band = win.create_gc(
            function=X.GXxor,
            foreground=screen.white_pixel ^ screen.black_pixel,
            line_width=2,
            subwindow_mode=X.IncludeInferiors,
        )
        win.map()
        d.sync()

        deadline = time.monotonic() + timeout_s

        def next_event() -> Any | None:
            while not d.pending_events():
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                select.select([d], [], [], min(left, 0.1))
            return d.next_event()

        while True:  # wait until mapped, or the grabs fail
            ev = next_event()
            if ev is None:
                return None
            if ev.type == X.MapNotify:
                break
        for _ in range(20):  # another app may hold a grab for a moment
            mask = X.ButtonPressMask | X.ButtonReleaseMask | X.PointerMotionMask
            if win.grab_pointer(False, mask, X.GrabModeAsync, X.GrabModeAsync, win, cursor, X.CurrentTime) == X.GrabSuccess:
                break
            time.sleep(0.05)
        else:
            return None
        win.grab_keyboard(False, X.GrabModeAsync, X.GrabModeAsync, X.CurrentTime)
        d.sync()

        escape = XK.XK_Escape
        start: tuple[int, int] | None = None
        cur: tuple[int, int] = (0, 0)

        def draw(a: tuple[int, int], b: tuple[int, int]) -> None:
            x0, x1 = sorted((a[0], b[0]))
            y0, y1 = sorted((a[1], b[1]))
            win.rectangle(band, x0, y0, max(1, x1 - x0), max(1, y1 - y0))

        while True:
            ev = next_event()
            if ev is None:
                return None
            if ev.type == X.KeyPress and d.keycode_to_keysym(ev.detail, 0) == escape:
                return None
            if ev.type == X.ButtonPress:
                if ev.detail != 1:
                    return None
                start = cur = (ev.event_x, ev.event_y)
            elif ev.type == X.MotionNotify and start is not None:
                draw(start, cur)  # xor: erase the old box
                cur = (ev.event_x, ev.event_y)
                draw(start, cur)
                d.flush()
            elif ev.type == X.ButtonRelease and ev.detail == 1 and start is not None:
                return _norm_rect(start, (ev.event_x, ev.event_y))
            elif ev.type == X.Expose and ev.count == 0:
                win.clear_area()
                if start is not None:
                    draw(start, cur)
    finally:
        try:
            d.ungrab_pointer(X.CurrentTime)
            d.ungrab_keyboard(X.CurrentTime)
            if win is not None:
                win.destroy()
            d.sync()
        finally:
            d.close()


def _portal_options(token: str) -> dict[str, Any]:
    from dbus_next import Variant

    return {"interactive": Variant("b", True), "handle_token": Variant("s", token)}


def _portal_request(timeout_s: float = _WAIT_TIMEOUT_S) -> tuple[int, dict[str, Any]]:
    """org.freedesktop.portal.Screenshot.Screenshot; returns the Request's (response, results)."""
    import asyncio

    async def run() -> tuple[int, dict[str, Any]]:
        from dbus_next import Message, MessageType
        from dbus_next.aio import MessageBus

        bus = await MessageBus().connect()
        try:
            token = f"ducky_snip_{os.getpid()}_{int(time.time() * 1000)}"
            sender = bus.unique_name.lstrip(":").replace(".", "_")
            req_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
            done: asyncio.Future = asyncio.get_running_loop().create_future()

            def on_msg(msg: Any) -> None:
                if (msg.message_type == MessageType.SIGNAL and msg.member == "Response"
                        and msg.path == req_path and not done.done()):
                    done.set_result(msg.body)

            bus.add_message_handler(on_msg)
            # Subscribe before calling so a fast reply is not missed.
            await bus.call(Message(
                destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus", member="AddMatch", signature="s",
                body=[f"type='signal',interface='org.freedesktop.portal.Request',"
                      f"member='Response',path='{req_path}'"],
            ))
            reply = await bus.call(Message(
                destination="org.freedesktop.portal.Desktop", path="/org/freedesktop/portal/desktop",
                interface="org.freedesktop.portal.Screenshot", member="Screenshot",
                signature="sa{sv}", body=["", _portal_options(token)],
            ))
            if reply.message_type == MessageType.ERROR:
                raise RuntimeError(f"{reply.error_name}: {reply.body}")
            code, results = await asyncio.wait_for(done, timeout_s)
            return int(code), dict(results)
        finally:
            bus.disconnect()

    return asyncio.run(run())


def _portal_result(code: int, results: dict[str, Any]) -> dict[str, Any]:
    """Portal Response -> our result. 0 = picked, 1 = cancelled, else failed."""
    from urllib.parse import unquote, urlparse

    from PIL import Image

    if code == 1:
        return dict(_CANCELLED)
    uri = results.get("uri")
    uri = getattr(uri, "value", uri)
    if code != 0 or not uri:
        return {"ok": False, "reason": "error", "error": f"portal response {code}"}
    # ponytail: the portal's file is left where the desktop saved it (it may be the user's Screenshots folder).
    with Image.open(unquote(urlparse(str(uri)).path)) as img:
        return _png_result(img.convert("RGB"))


def _portal_snip() -> dict[str, Any]:
    try:
        code, results = _portal_request()
    except TimeoutError:
        return dict(_CANCELLED)
    return _portal_result(code, results)
