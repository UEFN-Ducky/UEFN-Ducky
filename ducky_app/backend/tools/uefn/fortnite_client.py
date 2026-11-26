"""The real Fortnite client a UEFN session launches: find it, wait for it, capture it,
move the player. Part of the UEFN plugin (its register() imports this module).

Nothing here touches a client that is still loading: on Oct 2 2026 tools focused the
client and sent keys mid-load, its GPU hung and Fortnite closed ("The application has
hung"). Capture and input wait until the client's log says it is in play mode and
has settled; the window is restored only when minimized, never resized.
"""

from __future__ import annotations

import calendar
import io
import os
import re
import sys
import time
from typing import Any

from backend.tools.support.plugin_gate import plugin_mcp_tool
from backend.util.json_util import tool_json

_PLAYMODE = "we are in playmode"
_SETTLE_S = 15.0
_STAMP = re.compile(r"^\[(\d{4})\.(\d{2})\.(\d{2})-(\d{2})\.(\d{2})\.(\d{2})")
_KEYS = ("W", "A", "S", "D")


def _client_log() -> str:
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "FortniteGame", "Saved", "Logs", "FortniteGame.log")


def gameplay_loaded_for(path: str | None = None) -> float | None:
    """Seconds since the client logged that it is in play mode; None while it loads.
    The log is new for every client start, so an earlier session never counts."""
    try:
        with open(path or _client_log(), encoding="utf-8", errors="replace") as fh:
            line = next((ln for ln in fh if _PLAYMODE in ln), None)
    except OSError:
        return None
    if line is None:
        return None
    m = _STAMP.match(line)
    if not m:
        return 0.0
    logged = calendar.timegm(tuple(int(x) for x in m.groups()) + (0, 0, 0))  # log stamps are UTC
    return max(0.0, time.time() - logged)


def _user32():
    import ctypes
    from ctypes import wintypes

    u = ctypes.WinDLL("user32", use_last_error=True)
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.IsIconic.argtypes = [wintypes.HWND]
    u.SetForegroundWindow.argtypes = [wintypes.HWND]
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    return u


class NoClientWindow(RuntimeError):
    """No visible Fortnite client window: nothing is playing yet."""


def _client() -> tuple[Any, int, str]:
    """The one visible window titled "Fortnite" (never the editor or the launcher)."""
    if sys.platform != "win32":
        raise RuntimeError("Windows is required")
    import ctypes
    from ctypes import wintypes

    u = _user32()
    hits: list[tuple[int, str]] = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _):
        if u.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(u.GetWindowTextLengthW(hwnd) + 1)
            u.GetWindowTextW(hwnd, title, len(title))
            if title.value.strip().lower() == "fortnite":
                hits.append((hwnd, title.value))
        return True

    u.EnumWindows(proc(visit), 0)
    if not hits:
        raise NoClientWindow("Expected exactly one visible Fortnite client window; found 0")
    if len(hits) != 1:
        raise RuntimeError(f"Expected exactly one visible Fortnite client window; found {len(hits)}")
    return u, int(hits[0][0]), hits[0][1]


def _require_gameplay() -> None:
    age = gameplay_loaded_for()
    if age is None:
        raise RuntimeError(
            "Fortnite is still loading (no play mode in its log yet): wait with fortnite_client_wait, do not send input"
        )
    if age < _SETTLE_S:
        time.sleep(_SETTLE_S - age)


def _focus(u: Any, hwnd: int) -> None:
    # Restore only a minimized client: SW_RESTORE on a maximized or fullscreen window
    # resizes it, which forces the game's swapchain to resize.
    if u.IsIconic(hwnd):
        u.ShowWindow(hwnd, 9)
    if u.GetForegroundWindow() != hwnd:
        u.SetForegroundWindow(hwnd)
        time.sleep(0.25)
    if u.GetForegroundWindow() != hwnd:
        raise RuntimeError("Fortnite could not receive foreground focus")


def _shot(u: Any, hwnd: int, label: str) -> dict[str, Any]:
    import ctypes
    from ctypes import wintypes

    from PIL import ImageGrab

    from frontend.ui_web.tool_captures import save_capture_for_agents

    rect = wintypes.RECT()
    if not u.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError("Could not read the Fortnite window bounds")
    box = (rect.left, rect.top, rect.right, rect.bottom)
    if box[2] <= box[0] or box[3] <= box[1]:
        raise RuntimeError("The Fortnite window has no capture area")
    img = ImageGrab.grab(bbox=box, all_screens=True)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    saved = save_capture_for_agents(buf.getvalue(), prefix="fortnite_client")
    if not saved.get("ok"):
        raise RuntimeError(saved.get("error") or "No chat to save the capture into")
    path = str(saved.get("path") or saved.get("capture_path") or "")
    if not path:
        raise RuntimeError("The capture was not saved")
    return {"kind": "image", "path": path, "name": label}


def _on_pc() -> dict[str, Any] | None:
    """macOS: the Fortnite client runs on the account's PC (see window_view.uefn_on_pc)."""
    from frontend.window_view import uefn_on_pc

    return uefn_on_pc()


def client_status() -> dict[str, Any]:
    if (away := _on_pc()) is not None:
        return away
    try:
        u, hwnd, title = _client()
    except NoClientWindow:
        # A status answer, not a failure: no play session has opened the client yet.
        return {"ok": True, "running": False,
                "hint": "No Fortnite client is open. Start a play session in UEFN, then fortnite_client_wait."}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    age = gameplay_loaded_for()
    return {
        "ok": True,
        "running": True,
        "hwnd": hwnd,
        "title": title,
        "foreground": u.GetForegroundWindow() == hwnd,
        "gameplay_loaded": age is not None,
        "loaded_for_s": None if age is None else round(age, 1),
    }


def client_wait(timeout: float = 120.0) -> dict[str, Any]:
    if (away := _on_pc()) is not None:
        return away
    duration = min(max(float(timeout), 1.0), 120.0)
    deadline = time.monotonic() + duration
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = client_status()
        if last.get("ok") and (last.get("loaded_for_s") or 0) >= _SETTLE_S:
            return {**last, "gameplay_verified": False}
        time.sleep(1.0)
    return {
        "ok": False,
        "error": "Timed out waiting for the Fortnite client to load into play mode",
        "gameplay_loaded": bool(last.get("gameplay_loaded")),
        "last_status": last,
    }


def client_capture() -> dict[str, Any]:
    if (away := _on_pc()) is not None:
        return away
    try:
        u, hwnd, title = _client()
        _require_gameplay()
        _focus(u, hwnd)
        image = _shot(u, hwnd, "Fortnite client")
        return {"ok": True, "title": title, "files": [image], "path": image["path"]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _send_key(u: Any, key: str, *, up: bool) -> None:
    import ctypes
    from ctypes import wintypes

    pointer = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", pointer)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", pointer)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("data",)
        _fields_ = [("type", wintypes.DWORD), ("data", UNION)]

    u.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    scan = u.MapVirtualKeyW(ord(key), 0)
    KEYEVENTF_SCANCODE, KEYEVENTF_KEYUP = 8, 2
    event = INPUT(type=1, ki=KEYBDINPUT(0, scan, KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0), 0, 0))
    if u.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) != 1:
        raise RuntimeError("Windows rejected the keyboard input")


def client_move(key: str = "W", seconds: float = 1.5) -> dict[str, Any]:
    name = str(key or "").strip().upper()
    if name not in _KEYS:
        return {"ok": False, "error": "Movement key must be W, A, S or D"}
    duration = float(seconds)
    if not 0.1 <= duration <= 3.0:
        return {"ok": False, "error": "Hold time must be between 0.1 and 3 seconds"}
    if (away := _on_pc()) is not None:
        return away
    try:
        u, hwnd, _title = _client()
        _require_gameplay()
        _focus(u, hwnd)
        before = _shot(u, hwnd, "Before")
        lost_focus = False
        try:
            _send_key(u, name, up=False)
            deadline = time.monotonic() + duration
            while time.monotonic() < deadline:
                if u.GetForegroundWindow() != hwnd:
                    lost_focus = True  # never type into whatever took focus
                    break
                time.sleep(0.03)
        finally:
            _send_key(u, name, up=True)
        if lost_focus:
            raise RuntimeError("Fortnite lost focus during the move; the key was released")
        time.sleep(0.3)
        after = _shot(u, hwnd, "After")
        return {"ok": True, "key": name, "seconds": duration, "input_sent": True, "movement_verified": False,
                "before": before["path"], "after": after["path"], "files": [before, after]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# --------------------------------------------------------------------------- MCP tools


@plugin_mcp_tool("uefn")
def fortnite_client_status(pretty: bool = False) -> str:
    """Find the Fortnite client a UEFN session launched and whether it is in play mode. Never sends input.

    The window titled "Fortnite", never the editor or launcher.
    """
    return tool_json(client_status(), pretty=pretty)


@plugin_mcp_tool("uefn")
def fortnite_client_wait(timeout: float = 120.0, pretty: bool = False) -> str:
    """Wait (max 120 s) until the Fortnite client has loaded into play mode. Never focuses it or sends input.

    Read from its log, plus 15 s to settle. Then fortnite_client_capture to check the
    island with your own eyes.
    """
    return tool_json(client_wait(timeout), pretty=pretty)


@plugin_mcp_tool("uefn")
def fortnite_client_capture(pretty: bool = False) -> str:
    """Bring the loaded Fortnite client to the front and capture it into this chat. Not while it loads.

    Confirm the island, a controllable player and no menu or loading screen before moving.
    """
    return tool_json(client_capture(), pretty=pretty)


@plugin_mcp_tool("uefn")
def fortnite_client_move(key: str = "W", seconds: float = 1.5, pretty: bool = False) -> str:
    """Hold W/A/S/D in the loaded Fortnite client for 0.1–3 s, with before/after captures. Never left held.

    Always released; stops if the client loses focus. input_sent is not proof: prove
    the move from the two images against fixed landmarks, else report FAIL.
    """
    return tool_json(client_move(key, seconds), pretty=pretty)


# --------------------------------------------------------------------------- workflow nodes


def _cfg(ctx: dict[str, Any]) -> dict[str, Any]:
    return ctx.get("config") if isinstance(ctx.get("config"), dict) else {}


def register_nodes(api: Any) -> None:
    """uefn.client.wait / capture / move — same calls as the tools."""
    if not hasattr(api, "register_pipeline_node"):
        return
    api.register_pipeline_node("uefn.client.wait", lambda ctx: client_wait(float(_cfg(ctx).get("timeout") or 120)))
    api.register_pipeline_node("uefn.client.capture", lambda ctx: client_capture())
    api.register_pipeline_node(
        "uefn.client.move",
        lambda ctx: client_move(str(_cfg(ctx).get("key") or "W"), float(_cfg(ctx).get("seconds") or 1.5)),
    )
