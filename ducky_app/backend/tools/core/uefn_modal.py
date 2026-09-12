"""Dismiss UEFN Save/Yes modals from the Ducky host (outside the editor thread).

Epic MCP and in-editor Python run on UEFN's Slate/game thread. A modal Save
dialog blocks that thread, so ``execute_python`` / ``unreal__*`` hang and cannot
click the popup. This module talks Win32 from the host process instead.

What was verified against a live UEFN "Save Content" (SPackagesDialog):

* Slate keeps its widget tree private — UI Automation sees only the window
  (even after ``Accessibility.Enable 1``), so there is no button to invoke.
* Enter on the dialog window presses its blue default button ("Save Selected").
  Posted straight to the dialog HWND it needs no focus change; the keyboard
  fallback only fires once the dialog really is the foreground window, so a
  stray Enter can never land in the user's chat box.
* The Slate tick keeps running inside the modal loop, but the command that
  opened the dialog stays on the stack — so the host must press the button.
"""

from __future__ import annotations

import contextlib
import re
import sys
import threading
import time
from typing import Any, Callable, Iterator

SAVE_LISTENER_COMMANDS: frozenset[str] = frozenset(
    {
        "save_current_level",
        "save_all_dirty",
        "save_asset",
        "save_directory",
    }
)

_UEFN_EXE_HINTS = (
    "unrealeditorfortnite.exe",
    "unrealeditorfortnite-win64-shipping.exe",
    "unrealeditorfortnite-win64-debuggame.exe",
)

_SAVE_TITLE_HINTS = (
    "save content",
    "save package",
    "save packages",
    "save level",
    "save map",
    "save all",
    "save changes",
    "unsaved",
    "checkout",
    "source control",
    "dirty package",
)

# Whole words that turn a "save" title into something Enter must never confirm:
# destructive prompts, and pickers ("Save Level As", export/import/rename) where
# Enter would commit whatever path is typed.
_TITLE_REJECT_RE = re.compile(
    r"\b(delete|discard|uninstall|quit|exit|export|import|rename|as)\b"
)

_CONFIRM_EXACT = frozenset(
    {"save", "save all", "save selected", "yes", "ok", "apply"}
)
_BUTTON_REJECT = (
    "don't save",
    "dont save",
    "do not save",
    "cancel",
    "no",
    "discard",
    "delete",
    "abort",
    "save as",
)

# Automatic callers (bridge watchdog, 503 retry loop) may press at most once per
# cooldown, so a dialog that survives one Enter is not hammered every second.
_AUTO_COOLDOWN_SEC = 4.0
# Bound on waiting for an in-flight press when the watchdog block exits.
_STOP_JOIN_S = 5.0
_last_auto_press_at = 0.0
_auto_lock = threading.Lock()

# pid -> (is_uefn, checked_at). Pids recycle, so the answer expires.
_PID_CACHE_TTL_SEC = 60.0
_pid_cache: dict[int, tuple[bool, float]] = {}


def _norm_button(text: str) -> str:
    t = (text or "").replace("&", "").replace(".", "").strip().lower()
    return " ".join(t.split())


def is_save_dialog_title(title: str) -> bool:
    t = (title or "").strip().lower()
    if not t or _TITLE_REJECT_RE.search(t):
        return False
    return any(h in t for h in _SAVE_TITLE_HINTS)


def is_confirm_button(text: str) -> bool:
    t = _norm_button(text)
    if not t or any(bad in t for bad in _BUTTON_REJECT):
        return False
    return t in _CONFIRM_EXACT


def dismiss_uefn_save_modal(*, allow_bare_enter: bool = False) -> dict[str, Any]:
    """Click Save/Yes/OK on a UEFN confirm dialog, or Enter if that is the default.

    ``allow_bare_enter``: only the explicit MCP tool should set this. Auto-heal
    from a busy save must not send Enter to the main editor.
    """
    if sys.platform != "win32":
        return {"ok": False, "error": "Windows only", "clicked": False}
    try:
        return _dismiss_win32(allow_bare_enter=allow_bare_enter)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "clicked": False}


def find_uefn_save_dialog() -> dict[str, Any] | None:
    """Title of the UEFN Save prompt currently on screen, or None (read-only)."""
    if sys.platform != "win32":
        return None
    try:
        for win in _enum_uefn_windows():
            if win["cls"] == "UnrealWindow" and is_save_dialog_title(win["title"]):
                return {"title": win["title"], "hwnd": int(win["hwnd"])}
    except Exception:
        return None
    return None


def auto_dismiss_save_modal() -> dict[str, Any] | None:
    """Cooldown-limited, title-gated press for automatic callers (never bare Enter).

    Returns the dismiss info when a dialog was pressed, else None. Safe to call
    on every poll: with no matching dialog it is one window enumeration.
    """
    global _last_auto_press_at
    with _auto_lock:
        if time.time() - _last_auto_press_at < _AUTO_COOLDOWN_SEC:
            return None
        info = dismiss_uefn_save_modal(allow_bare_enter=False)
        if info.get("clicked") or info.get("sent_enter"):
            _last_auto_press_at = time.time()
            return info
    return None


@contextlib.contextmanager
def save_modal_watchdog(
    label: str,
    *,
    poll_sec: float = 1.0,
    on_press: Callable[[dict[str, Any]], None] | None = None,
) -> Iterator[list[dict[str, Any]]]:
    """Press UEFN Save prompts while the caller blocks on the editor.

    A daemon thread checks right away and then every ``poll_sec`` until the
    ``with`` block exits, so the blocked host call (listener POST, Verse
    workflow request) needs no cooperation. Yields the list of presses made.
    """
    events: list[dict[str, Any]] = []
    if sys.platform != "win32":
        yield events
        return
    stop = threading.Event()

    def _run() -> None:
        while not stop.is_set():
            info = auto_dismiss_save_modal()
            if info:
                event = {**info, "label": label, "at": time.time()}
                events.append(event)
                if on_press is not None:
                    try:
                        on_press(event)
                    except Exception:
                        pass
            if stop.wait(poll_sec):
                return

    thread = threading.Thread(target=_run, name=f"uefn-modal-watchdog:{label}", daemon=True)
    thread.start()
    try:
        yield events
    finally:
        stop.set()
        # Wait for the tick in flight to finish. Setting the flag alone leaves a
        # thread that is already inside auto_dismiss_save_modal() free to press
        # Enter *after* the caller stopped wanting presses — into whatever dialog
        # the user opened next. stop.wait() returns as soon as the flag is set,
        # so this costs nothing unless a press is genuinely mid-flight.
        thread.join(timeout=_STOP_JOIN_S)


# ---------------------------------------------------------------------------
# Win32
# ---------------------------------------------------------------------------


def _user32():
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsWindowEnabled.argtypes = [wintypes.HWND]
    user32.IsWindowEnabled.restype = wintypes.BOOL
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.SendMessageW.argtypes = [
        wintypes.HWND,
        ctypes.c_uint,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.SendMessageW.restype = wintypes.LPARAM
    user32.PostMessageW.argtypes = [
        wintypes.HWND,
        ctypes.c_uint,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostMessageW.restype = wintypes.BOOL
    return user32


def _pid_is_uefn(pid: int) -> bool:
    now = time.time()
    hit = _pid_cache.get(pid)
    if hit is not None and now - hit[1] < _PID_CACHE_TTL_SEC:
        return hit[0]
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    image = ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        try:
            size = wintypes.DWORD(32768)
            buf = ctypes.create_unicode_buffer(32768)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                image = buf.value
        finally:
            kernel32.CloseHandle(handle)
    name = image.replace("\\", "/").rsplit("/", 1)[-1].lower()
    is_uefn = name in _UEFN_EXE_HINTS or name.startswith("unrealeditorfortnite")
    _pid_cache[pid] = (is_uefn, now)
    return is_uefn


def _enum_uefn_windows() -> list[dict[str, Any]]:
    """Visible top-level windows of the UEFN process (title, class, owner)."""
    import ctypes
    from ctypes import wintypes

    user32 = _user32()
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    GW_OWNER = 4

    def _text(hwnd) -> str:
        n = int(user32.GetWindowTextLengthW(hwnd) or 0)
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def _class_name(hwnd) -> str:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        return buf.value

    windows: list[dict[str, Any]] = []

    def _enum_top(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value or not _pid_is_uefn(int(pid.value)):
            return True
        cls = _class_name(hwnd)
        windows.append(
            {
                "hwnd": hwnd,
                "title": _text(hwnd),
                "cls": cls,
                "owned": bool(user32.GetWindow(hwnd, GW_OWNER)),
                "dialog_cls": cls == "#32770",
            }
        )
        return True

    user32.EnumWindows(WNDENUMPROC(_enum_top), 0)
    return windows


def _dismiss_win32(*, allow_bare_enter: bool) -> dict[str, Any]:
    import ctypes
    from ctypes import wintypes

    user32 = _user32()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
    user32.EnumChildWindows.restype = wintypes.BOOL

    BM_CLICK = 0x00F5
    SW_RESTORE = 9
    KEYEVENTF_KEYUP = 2
    VK_RETURN = 0x0D
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    ENTER_SCANCODE = 0x1C

    def _text(hwnd) -> str:
        n = int(user32.GetWindowTextLengthW(hwnd) or 0)
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    windows = _enum_uefn_windows()

    buttons: list[tuple[Any, str, Any]] = []  # (button_hwnd, label, parent)
    parent_hwnd: Any = None

    def _enum_child(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or not user32.IsWindowEnabled(hwnd):
            return True
        label = _text(hwnd)
        if is_confirm_button(label):
            buttons.append((hwnd, label, parent_hwnd))
        return True

    child_proc = WNDENUMPROC(_enum_child)
    for win in windows:
        parent_hwnd = win["hwnd"]
        user32.EnumChildWindows(win["hwnd"], child_proc, 0)

    def _force_fg(hwnd) -> bool:
        fg = user32.GetForegroundWindow()
        fg_tid = user32.GetWindowThreadProcessId(fg, None)
        our_tid = kernel32.GetCurrentThreadId()
        attached = False
        if fg_tid and our_tid and fg_tid != our_tid:
            attached = bool(user32.AttachThreadInput(our_tid, fg_tid, True))
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        if attached:
            user32.AttachThreadInput(our_tid, fg_tid, False)
        return user32.GetForegroundWindow() == hwnd

    def _closed(hwnd) -> bool:
        return not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd)

    def _enter(hwnd) -> dict[str, Any]:
        # Slate reads the posted key from its own message pump, so the dialog
        # need not own the foreground. The key up carries the "previous state"
        # and "transition" bits like a real release.
        down = 1 | (ENTER_SCANCODE << 16)
        up = down | (1 << 30) | (1 << 31)
        user32.PostMessageW(hwnd, WM_KEYDOWN, VK_RETURN, down)
        user32.PostMessageW(hwnd, WM_KEYUP, VK_RETURN, up)
        deadline = time.time() + 0.6
        while time.time() < deadline:
            if _closed(hwnd):
                return {"sent_enter": True, "method": "enter_posted"}
            time.sleep(0.05)
        # Fallback: a real keystroke — but only when this dialog has the keyboard.
        if not _force_fg(hwnd):
            return {"sent_enter": False, "method": "enter_foreground_denied"}
        user32.keybd_event(VK_RETURN, 0, 0, 0)
        user32.keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, 0)
        return {"sent_enter": True, "method": "enter_foreground"}

    base = {"ok": True, "uefn_windows": len(windows), "clicked": False, "sent_enter": False}

    if buttons:
        hwnd, label, parent = buttons[0]
        _force_fg(parent or hwnd)
        user32.SendMessageW(hwnd, BM_CLICK, 0, 0)
        parent_title = ""
        for win in windows:
            if win["hwnd"] == parent:
                parent_title = win["title"]
                break
        return {
            **base,
            "clicked": True,
            "method": "bm_click",
            "button": label,
            "window_title": parent_title,
        }

    # Slate dialogs carry no Win32 buttons; Enter presses their default button.
    dialogs = [
        w for w in windows if w["cls"] == "UnrealWindow" and is_save_dialog_title(w["title"])
    ]
    if dialogs:
        target = dialogs[0]
        return {**base, **_enter(target["hwnd"]), "window_title": target["title"]}

    if allow_bare_enter and windows:
        # ponytail: Slate modals often have no Win32 title/buttons; default
        # button is Save. Ceiling: Enter hits whatever UEFN has focused if no
        # modal is up. Upgrade: UI Automation hit-test of Slate widgets.
        target = next((w for w in windows if w["owned"]), windows[0])
        return {**base, **_enter(target["hwnd"]), "window_title": target["title"]}

    return {**base, "reason": "no save dialog"}
