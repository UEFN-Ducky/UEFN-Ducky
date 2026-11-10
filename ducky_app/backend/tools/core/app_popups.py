"""Popups of any desktop app Ducky drives: find them, show them with numbered buttons, press one.

UEFN has its own (``uefn_popups``); this is the same idea for every other program a plugin or
nested MCP server drives (After Effects, Premiere Pro, Media Encoder, Photoshop, Illustrator,
Blender, Unity, Godot, Roblox Studio, Unreal Editor, or any exe by name). A modal popup holds
the app's script engine: a call behind it waits until a button is pressed.

A popup is any visible top-level window of the app other than its main window. Its buttons:
standard Win32 dialogs list real Button controls (exact rect and label, pressed with
BM_CLICK, no pointer move); custom-drawn ones (Premiere's DroverLord, Qt, Slate) are found in a
capture like UEFN's (``uefn_popups.find_buttons``) and pressed with a real click that gives the
person back their window and pointer. ``press_lone_ok`` presses only blocking message boxes
whose single button is the way on, never one whose title could commit or discard something.
"""

from __future__ import annotations

import ctypes
import io
import re
import sys
import time
from typing import Any

# name -> exe names (lower case). Any other exe works too: app="foo.exe".
APPS: dict[str, tuple[str, ...]] = {
    "aftereffects": ("afterfx.exe",),
    "premiere": ("adobe premiere pro.exe",),
    "mediaencoder": ("adobe media encoder.exe",),
    "photoshop": ("photoshop.exe",),
    "illustrator": ("illustrator.exe",),
    "blender": ("blender.exe",),
    "unity": ("unity.exe",),
    "godot": ("godot.exe", "godot_v4.exe", "godot_v4.3-stable_win64.exe", "godot_v4.4-stable_win64.exe"),
    "roblox": ("robloxstudiobeta.exe",),
    "unreal": ("unrealeditor.exe",),
    "uefn": ("unrealeditorfortnite-win64-shipping.exe",),
}
_ALIASES = {"ae": "aftereffects", "after effects": "aftereffects", "pr": "premiere", "premiere pro": "premiere",
            "ame": "mediaencoder", "media encoder": "mediaencoder", "ps": "photoshop", "ai": "illustrator"}
_REJECT_RE = re.compile(
    r"\b(delete|discard|remove|overwrite|replace|uninstall|quit|exit|close|save|publish|upload|purchase"
    r"|buy|sign in|log in|license|activate|subscription|update available)\b"
)
_MIN_SIDE = 60          # tooltips and drag handles are smaller
_LONE_MAX_W = 1400      # a message box is small; a floating panel is not
_LONE_MAX_H = 900


def exe_names(app: str) -> tuple[str, ...]:
    key = str(app or "").strip().lower()
    key = _ALIASES.get(key, key)
    if key in APPS:
        return APPS[key]
    if key.endswith(".exe"):
        return (key,)
    return tuple(name for names in APPS.values() for name in names) if not key else ()


def _user32() -> Any:
    return ctypes.windll.user32


def _pids(names: tuple[str, ...]) -> dict[int, str]:
    if sys.platform != "win32" or not names:
        return {}
    from ctypes import wintypes

    class PE(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    if snap in (-1, ctypes.c_void_p(-1).value):
        return {}
    out: dict[int, str] = {}
    try:
        e = PE()
        e.dwSize = ctypes.sizeof(PE)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() in names:
                out[int(e.th32ProcessID)] = e.szExeFile
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return out


def _text(hwnd: int) -> str:
    u = _user32()
    n = u.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _cls(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32().GetClassNameW(hwnd, buf, 256)
    return buf.value


def _rect(hwnd: int) -> dict[str, int]:
    from ctypes import wintypes

    r = wintypes.RECT()
    _user32().GetWindowRect(hwnd, ctypes.byref(r))
    return {"left": r.left, "top": r.top, "right": r.right, "bottom": r.bottom,
            "width": r.right - r.left, "height": r.bottom - r.top}


def _top_windows(pids: dict[int, str]) -> list[dict[str, Any]]:
    from ctypes import wintypes

    u = _user32()
    found: list[dict[str, Any]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _lp):  # type: ignore[no-untyped-def]
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) in pids and u.IsWindowVisible(hwnd):
            h = int(hwnd)
            found.append({"hwnd": h, "pid": int(pid.value), "exe": pids[int(pid.value)], "title": _text(h),
                          "cls": _cls(h), "owner": int(u.GetWindow(hwnd, 4) or 0),
                          "enabled": bool(u.IsWindowEnabled(hwnd)), "rect": _rect(h)})
        return True

    u.EnumWindows(each, 0)
    return found


def win32_buttons(hwnd: int) -> list[dict[str, Any]]:
    """Real Button controls of a standard dialog, left to right: label, rect, nx/ny, default."""
    if sys.platform != "win32":
        return []
    from ctypes import wintypes

    u = _user32()
    box = _rect(hwnd)
    out: list[dict[str, Any]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(child, _lp):  # type: ignore[no-untyped-def]
        c = int(child)
        if _cls(c).lower() == "button" and u.IsWindowVisible(child):
            style = u.GetWindowLongW(child, -16) & 0x0F
            if style in (0, 1):  # BS_PUSHBUTTON / BS_DEFPUSHBUTTON (not check boxes or radios)
                r = _rect(c)
                if box["width"] and box["height"]:
                    out.append({"hwnd": c, "label": _text(c).replace("&", ""), "primary": style == 1,
                                "nx": round((r["left"] + r["width"] / 2 - box["left"]) / box["width"], 4),
                                "ny": round((r["top"] + r["height"] / 2 - box["top"]) / box["height"], 4),
                                "x": r["left"]})
        return True

    u.EnumChildWindows(hwnd, each, 0)
    out.sort(key=lambda b: b["x"])
    return out


def _static_text(hwnd: int) -> str:
    """What a standard dialog says (its Static / Edit children)."""
    from ctypes import wintypes

    u = _user32()
    parts: list[str] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(child, _lp):  # type: ignore[no-untyped-def]
        if _cls(int(child)).lower() in ("static", "edit") and u.IsWindowVisible(child):
            t = _text(int(child)).strip()
            if t:
                parts.append(t)
        return True

    u.EnumChildWindows(hwnd, each, 0)
    return "\n".join(parts)[:1500]


def list_popups(app: str = "") -> list[dict[str, Any]]:
    """Each app's windows besides its main one. ``blocking``: the main window is disabled
    (a modal is up), which is when the app's scripts stop running."""
    if sys.platform != "win32":
        return []
    pids = _pids(exe_names(app))
    if not pids:
        return []
    by_pid: dict[int, list[dict[str, Any]]] = {}
    for w in _top_windows(pids):
        by_pid.setdefault(w["pid"], []).append(w)
    out: list[dict[str, Any]] = []
    for pid, wins in by_pid.items():
        owners = [w for w in wins if not w["owner"]]
        main = max(owners or wins, key=lambda w: w["rect"]["width"] * w["rect"]["height"])
        blocked = not main["enabled"]
        mr = main["rect"]
        for w in wins:
            r = w["rect"]
            if w["hwnd"] == main["hwnd"] or r["width"] < _MIN_SIDE or r["height"] < _MIN_SIDE:
                continue
            if r["width"] >= mr["width"] * 0.9 and r["height"] >= mr["height"] * 0.8:
                continue  # a layer over the whole app (Premiere's home screen), not a popup
            out.append({"hwnd": w["hwnd"], "app": w["exe"], "title": w["title"], "class": w["cls"],
                        "rect": r, "blocking": blocked, "enabled": w["enabled"],
                        "main_title": main["title"]})
    return out


def _capture(popup: dict[str, Any]) -> Any:
    from backend.tools.core.uefn_popups import _grab

    return _grab(popup["rect"], popup["hwnd"])


def find_buttons_any(img: Any) -> list[dict[str, Any]]:
    """Buttons in a drawn dialog, filled (UEFN, a blue default) or outlined (Premiere's
    Don't Save / Cancel pills, dark inside), left to right, each {x0, y0, x1, y1, nx, ny,
    primary}. Measured along the button's edge, so an outline is not mistaken for a line."""
    from backend.tools.core.uefn_popups import _background, _is_blue, _luma, find_buttons

    img = img.convert("RGB")
    w, h = img.size
    px = img.load()
    bg = _background(img)

    def lit(x: int, y: int) -> bool:
        p = px[x, y]
        return _is_blue(p) or _luma(p) >= bg + 9

    def runs(y: int) -> list[tuple[int, int]]:
        out, start = [], None
        for x in range(w + 1):
            on = x < w and lit(x, y)
            if on and start is None:
                start = x
            elif not on and start is not None:
                if 36 <= x - start < w * 0.6:
                    out.append((start, x - 1))
                start = None
        return out

    # A button is a top edge and a bottom edge of the same width 12..70 px apart: true for a
    # filled one (every row in between too) and a rounded outline (Premiere's pills).
    by_row = {y: runs(y) for y in range(int(h * 0.4), h - 1)}
    found: list[dict[str, Any]] = []
    for y, row in by_row.items():
        for x0, x1 in row:
            if any(b["x0"] - 14 <= x0 <= b["x1"] and b["y0"] <= y <= b["y1"] for b in found):
                continue  # inside a button already found
            match = None
            for y2 in range(y + 12, min(h - 1, y + 71)):
                for a0, a1 in by_row.get(y2, []):
                    if abs(a0 - x0) <= 6 and abs(a1 - x1) <= 6:
                        match = y2
            if match is None:
                continue
            cy = (y + match) // 2
            blue = sum(1 for xx in range(x0, x1 + 1, 3) if _is_blue(px[xx, cy]) or _is_blue(px[xx, y]))
            found.append({"x0": x0 - 8, "y0": y, "x1": x1 + 8, "y1": match,
                          "nx": round((x0 + x1) / 2 / w, 4), "ny": round(cy / h, 4),
                          "primary": blue * 3 >= (x1 - x0) // 3})
    if not found:
        return find_buttons(img)
    # One row of buttons: keep the row that has the most of them (dialog buttons line up).
    rows: dict[int, list[dict[str, Any]]] = {}
    for b in found:
        key = next((k for k in rows if abs(k - b["ny"] * h) < 10), int(b["ny"] * h))
        rows.setdefault(key, []).append(b)
    row = max(rows.values(), key=lambda r: (len(r), max(x["ny"] for x in r)))
    return sorted(row, key=lambda b: b["x0"])


def _buttons(popup: dict[str, Any], img: Any = None) -> tuple[list[dict[str, Any]], Any]:
    real = win32_buttons(popup["hwnd"])
    if real:
        return real, img
    img = img if img is not None else _capture(popup)
    return find_buttons_any(img), img


def describe(popup: dict[str, Any], *, save: bool = True) -> dict[str, Any]:
    """One popup with its numbered buttons (labels when the dialog has real ones), what it
    says, and (save=True) the numbered capture in this chat."""
    img = None
    try:
        img = _capture(popup)
    except Exception:
        img = None
    buttons, img = _buttons(popup, img)
    out = {**popup, "buttons": [{"n": i, "label": b.get("label", ""), "nx": b["nx"], "ny": b["ny"],
                                 "primary": bool(b.get("primary"))} for i, b in enumerate(buttons, start=1)]}
    said = _static_text(popup["hwnd"])
    if said:
        out["text"] = said
    if save and img is not None:
        path = _save(img, buttons)
        if path:
            out["path"] = path
    return out


def _save(img: Any, buttons: list[dict[str, Any]]) -> str:
    try:
        from backend.tools.core.uefn_popups import annotate
        from frontend.ui_web.tool_captures import save_capture_for_agents

        w, h = img.size
        boxes = [b if "x0" in b else {**b, "x0": int(b["nx"] * w) - 30, "x1": int(b["nx"] * w) + 30,
                                      "y0": int(b["ny"] * h) - 10, "y1": int(b["ny"] * h) + 10} for b in buttons]
        buf = io.BytesIO()
        annotate(img, boxes).save(buf, format="PNG")
        saved = save_capture_for_agents(buf.getvalue(), prefix="app_popup")
        return str(saved.get("path") or "") if saved.get("ok") else ""
    except Exception:
        return ""


def _find(hwnd: int) -> dict[str, Any] | None:
    return next((p for p in list_popups() if p["hwnd"] == int(hwnd)), None)


def press(hwnd: int, n: int) -> dict[str, Any]:
    """Press button ``n`` (1-based, left to right) of an app popup; report whether it closed."""
    popup = _find(hwnd)
    if popup is None:
        return {"ok": False, "error": f"no app popup {hwnd} on screen (ducky_app_popups lists them)"}
    buttons, _img = _buttons(popup)
    if not 1 <= int(n) <= len(buttons):
        return {"ok": False, "error": f"button {n} not found; this popup shows {len(buttons)}", "title": popup["title"]}
    b = buttons[int(n) - 1]
    if b.get("hwnd"):
        _user32().SendMessageW(int(b["hwnd"]), 0x00F5, 0, 0)  # BM_CLICK: no pointer, no focus change
    else:
        from backend.tools.core import uefn_modal
        from frontend.window_view import inject_key, inject_pointer

        person_had, pointer = uefn_modal.foreground_window(), uefn_modal.cursor_pos()
        try:
            # A drawn dialog ignores a click that only activates it (Premiere 2027's File
            # Import Failure took four): bring it in front first, like a person would.
            uefn_modal._force_foreground(int(hwnd), restore=False)
            time.sleep(0.15)
            inject_pointer(int(hwnd), "down", b["nx"], b["ny"])
            inject_pointer(int(hwnd), "up", b["nx"], b["ny"])
            if not _wait_closed(hwnd, 1.5) and b.get("primary"):
                # The default button answers Enter, wherever its pixels are.
                inject_key(int(hwnd), "Enter", down=True)
                inject_key(int(hwnd), "Enter", down=False)
                _wait_closed(hwnd, 1.5)
        finally:
            uefn_modal.set_cursor_pos(pointer)
            uefn_modal.give_foreground_back(person_had, popup=int(hwnd))
    closed = _wait_closed(hwnd, 2.0)
    return {"ok": True, "title": popup["title"], "app": popup["app"], "pressed": int(n),
            "label": b.get("label", ""), "closed": closed,
            "open_popups": [p["title"] for p in list_popups(popup["app"])]}


def _wait_closed(hwnd: int, seconds: float) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if _find(hwnd) is None:
            return True
        time.sleep(0.1)
    return _find(hwnd) is None


def is_lone_ok(popup: dict[str, Any], buttons: list[dict[str, Any]]) -> bool:
    """A blocking message box whose only button is the way on (OK / Close / Continue).

    Only with a window title to judge it by (Premiere's 'Save project' prompt draws its
    title inside and has no window title: never pressed by itself), and never a title or
    label about saving, deleting, quitting, signing in or buying."""
    title = str(popup.get("title") or "").strip().lower()
    label = str((buttons[0].get("label") if buttons else "") or "").strip().lower()
    if not popup.get("blocking") or not popup.get("enabled", True) or len(buttons) != 1:
        return False
    if not title or _REJECT_RE.search(title) or _REJECT_RE.search(label):
        return False
    r = popup.get("rect") or {}
    return r.get("width", 0) <= _LONE_MAX_W and r.get("height", 0) <= _LONE_MAX_H


def press_lone_ok(app: str, *, skip: frozenset[int] = frozenset()) -> list[dict[str, Any]]:
    """Press the single button of each blocking message box of ``app`` (what a stalled Ducky
    call does). Keeps each one's numbered capture and text in the answer."""
    done: list[dict[str, Any]] = []
    for popup in list_popups(app):
        if popup["hwnd"] in skip or not popup.get("blocking"):
            continue
        info = describe(popup)
        buttons = [{"label": b["label"], "nx": b["nx"], "ny": b["ny"]} for b in info["buttons"]]
        if not is_lone_ok(popup, buttons):
            continue
        result = press(popup["hwnd"], 1)
        done.append({**result, "text": info.get("text", ""), "path": info.get("path", "")})
    return done


def stall_report(app: str, *, press_ok: bool = True) -> dict[str, Any]:
    """For a call that stopped answering: press the app's lone-OK message boxes, then list
    what is still up (each with numbered buttons for ``ducky_app_popup_press``)."""
    pressed = press_lone_ok(app) if press_ok else []
    still = [describe(p) for p in list_popups(app)]
    return {"pressed": pressed, "popups": still, "blocking": any(p.get("blocking") for p in still)}
