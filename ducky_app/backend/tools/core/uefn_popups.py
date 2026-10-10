"""Find and answer UEFN popups from the Ducky host.

UEFN draws its dialogs with Slate: no Win32 buttons and nothing for UI Automation
(see uefn_modal.py). So a popup's buttons are found in a screenshot instead. They
are solid rectangles along the bottom of the dialog, the dark grey ones and the
blue default; text never forms a run that wide. The AI gets the capture with each
button boxed and numbered and presses one by number (``uefn_popups`` /
``uefn_popup_press``). The popups UEFN shows when a project opens with Verse
errors are answered by rule, so an agent never sits behind them:

* Verse Build Errors → Skip Rebuild, then Data Loss Warning → Skip Rebuild & Continue
* Verse Validation Errors → Continue

Continuing leaves the open level's Verse devices broken in memory, and saving the
level then breaks them for good. From that press until Verse builds clean and the
map is reloaded from disk, Ducky does not save (see ``verse_skip_active``).
"""

from __future__ import annotations

import io
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

# title (lower) → how many buttons the dialog has, which one to press (1-based, left
# to right) and where its blue default button sits, to recognise the layout.
KNOWN_POPUPS: dict[str, dict[str, Any]] = {
    "verse build errors": {"buttons": 4, "press": 3, "primary": 4, "label": "Skip Rebuild"},
    "data loss warning": {"buttons": 2, "press": 1, "primary": None, "label": "Skip Rebuild & Continue"},
    "verse validation errors": {"buttons": 4, "press": 4, "primary": 2, "label": "Continue"},
}

_MAIN_TITLE = "unreal editor for fortnite"
_MIN_BUTTON_W = 36
_MIN_BUTTON_H = 14


# --------------------------------------------------------------------------- buttons


def _is_blue(px: tuple[int, int, int]) -> bool:
    r, g, b = px[:3]
    return b >= 150 and b - r >= 70 and g >= 60


def _luma(px: tuple[int, int, int]) -> float:
    r, g, b = px[:3]
    return 0.299 * r + 0.587 * g + 0.114 * b


def _background(img: Any) -> float:
    """Luminance of the dialog's own fill, from the bottom band's most common shade."""
    w, h = img.size
    px = img.load()
    counts: dict[int, int] = {}
    for y in range(int(h * 0.6), h, 2):
        for x in range(0, w, 3):
            key = int(_luma(px[x, y])) // 2
            counts[key] = counts.get(key, 0) + 1
    return max(counts, key=counts.get) * 2 + 1 if counts else 36.0


def find_buttons(img: Any) -> list[dict[str, Any]]:
    """Buttons in a dialog capture (PIL image), left to right.

    Each: ``{x0, y0, x1, y1, nx, ny, primary}``; nx/ny are the centre as 0..1 of the
    image, which is what ``uefn_window_click`` takes for that window.
    """
    img = img.convert("RGB")
    w, h = img.size
    px = img.load()
    bg = _background(img)

    def lit(x: int, y: int) -> bool:
        p = px[x, y]
        return _is_blue(p) or _luma(p) >= bg + 9

    best: list[tuple[int, int]] = []
    best_y = -1
    for y in range(int(h * 0.5), h - 2):
        runs: list[tuple[int, int]] = []
        start = None
        for x in range(w):
            if lit(x, y):
                if start is None:
                    start = x
            elif start is not None:
                if x - start >= _MIN_BUTTON_W:
                    runs.append((start, x - 1))
                start = None
        if start is not None and w - start >= _MIN_BUTTON_W:
            runs.append((start, w - 1))
        runs = [r for r in runs if r[1] - r[0] < w * 0.6]
        if len(runs) > len(best) or (len(runs) == len(best) and runs and y > best_y and best_y < 0):
            best, best_y = runs, y
    buttons: list[dict[str, Any]] = []
    for x0, x1 in best:
        cx = (x0 + x1) // 2
        y0 = best_y
        while y0 > 0 and lit(cx, y0 - 1):
            y0 -= 1
        y1 = best_y
        while y1 < h - 1 and lit(cx, y1 + 1):
            y1 += 1
        if y1 - y0 + 1 < _MIN_BUTTON_H:
            continue
        cy = (y0 + y1) // 2
        blue = sum(1 for x in range(x0, x1 + 1, 3) if _is_blue(px[x, cy]))
        buttons.append({
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "nx": round((x0 + x1) / 2 / w, 4), "ny": round(cy / h, 4),
            "primary": blue * 3 >= (x1 - x0) // 2,
        })
    return buttons


def known_rule(title: str, buttons: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The rule for a known popup, only when its layout matches what the rule expects."""
    rule = KNOWN_POPUPS.get((title or "").strip().lower())
    if rule is None or len(buttons) != rule["buttons"]:
        return None
    primary = [i + 1 for i, b in enumerate(buttons) if b.get("primary")]
    if rule["primary"] is not None and primary != [rule["primary"]]:
        return None
    if rule["primary"] is None and primary:
        return None
    return rule


def annotate(img: Any, buttons: list[dict[str, Any]]) -> Any:
    """A copy of the capture with each button boxed and numbered, for the AI to read."""
    from PIL import ImageDraw

    out = img.convert("RGB").copy()
    draw = ImageDraw.Draw(out)
    for i, b in enumerate(buttons, start=1):
        draw.rectangle((b["x0"] - 2, b["y0"] - 2, b["x1"] + 2, b["y1"] + 2), outline=(255, 64, 160), width=2)
        tag = (b["x0"] - 2, max(0, b["y0"] - 16), b["x0"] + 14, max(14, b["y0"] - 2))
        draw.rectangle(tag, fill=(255, 64, 160))
        draw.text((tag[0] + 4, tag[1] + 1), str(i), fill=(255, 255, 255))
    return out


# --------------------------------------------------------------------------- windows


def list_popups() -> list[dict[str, Any]]:
    """UEFN's windows other than the main editor (dialogs, popups), with their rect."""
    if sys.platform != "win32":
        return []
    from backend.tools.core.uefn_modal import _enum_uefn_windows
    from backend.tools.core.uefn_windows import _rect

    import ctypes

    windows = _enum_uefn_windows()
    # A modal popup disables the main editor window: that is what "blocking" means.
    # Floating tool windows (Message Log) leave it enabled.
    main = [w for w in windows if str(w.get("title") or "").strip().lower() == _MAIN_TITLE]
    blocked = bool(main) and not ctypes.windll.user32.IsWindowEnabled(int(main[0]["hwnd"]))
    out = []
    for win in windows:
        title = str(win.get("title") or "")
        if win.get("cls") != "UnrealWindow" or title.strip().lower() == _MAIN_TITLE:
            continue
        rect = _rect(int(win["hwnd"]))
        if rect.get("width", 0) <= 0 or rect.get("height", 0) <= 0:
            continue
        out.append({"hwnd": int(win["hwnd"]), "title": title, "rect": rect, "blocking": blocked})
    return out


def _print_window(hwnd: int, width: int, height: int) -> Any | None:
    """The window's own pixels (PrintWindow, full content), even where another window
    covers it. None when Windows returns nothing usable."""
    import ctypes
    from ctypes import wintypes

    from PIL import Image

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.GetWindowDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.PrintWindow.restype = wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    src = user32.GetWindowDC(hwnd)
    if not src:
        return None
    mem = gdi32.CreateCompatibleDC(src)
    bmp = gdi32.CreateCompatibleBitmap(src, width, height)
    old = gdi32.SelectObject(mem, bmp)
    try:
        PW_RENDERFULLCONTENT = 2
        if not user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT):
            return None
        header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(width * height * 4)
        if gdi32.GetDIBits(mem, bmp, 0, height, buf, ctypes.byref(header), 0) != height:
            return None
        img = Image.frombuffer("RGB", (width, height), buf.raw, "raw", "BGRX", 0, 1)
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, src)
    lo, hi = img.convert("L").getextrema()
    return img if hi - lo > 24 else None  # all one shade: nothing was drawn


def _grab(rect: dict[str, Any], hwnd: int = 0) -> Any:
    """A capture of the popup. Its own pixels first: Ducky's window (or any other) on
    top of it hid the Continue button from a screen grab, live on Oct 2 2026."""
    if hwnd and sys.platform == "win32":
        try:
            img = _print_window(int(hwnd), int(rect["width"]), int(rect["height"]))
            if img is not None:
                return img
        except Exception:
            pass
    from PIL import ImageGrab

    box = (int(rect["left"]), int(rect["top"]), int(rect["right"]), int(rect["bottom"]))
    try:
        return ImageGrab.grab(bbox=box, all_screens=True)
    except TypeError:
        return ImageGrab.grab(bbox=box)


def _popup(hwnd: int) -> dict[str, Any] | None:
    return next((p for p in list_popups() if p["hwnd"] == int(hwnd)), None)


def describe_popup(popup: dict[str, Any], *, save: bool = True) -> dict[str, Any]:
    """Capture one popup: its buttons, the known rule if any, and (save=True) the
    numbered capture in this chat's attachments."""
    img = _grab(popup["rect"], popup["hwnd"])
    buttons = find_buttons(img)
    rule = known_rule(popup["title"], buttons)
    out: dict[str, Any] = {
        **popup,
        "buttons": [{"n": i, "nx": b["nx"], "ny": b["ny"], "primary": b["primary"]} for i, b in enumerate(buttons, start=1)],
    }
    if rule:
        out["known"] = {"press": rule["press"], "label": rule["label"]}
    if save:
        try:
            from frontend.ui_web.tool_captures import save_capture_for_agents

            buf = io.BytesIO()
            annotate(img, buttons).save(buf, format="PNG")
            saved = save_capture_for_agents(buf.getvalue(), prefix="uefn_popup")
            if saved.get("ok"):
                out["path"] = str(saved.get("path") or "")
        except Exception:
            pass
    return out


def press_button(hwnd: int, n: int) -> dict[str, Any]:
    """Press button ``n`` (1-based, left to right) of a UEFN popup, then report whether it closed."""
    popup = _popup(hwnd)
    if popup is None:
        return {"ok": False, "error": f"no UEFN popup {hwnd} on screen (uefn_popups lists them)"}
    buttons = find_buttons(_grab(popup["rect"], popup["hwnd"]))
    if not 1 <= int(n) <= len(buttons):
        return {"ok": False, "error": f"button {n} not found; this popup shows {len(buttons)}", "title": popup["title"]}
    b = buttons[int(n) - 1]
    from frontend.window_view import inject_pointer

    inject_pointer(int(hwnd), "down", b["nx"], b["ny"])
    inject_pointer(int(hwnd), "up", b["nx"], b["ny"])
    closed = False
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if _popup(hwnd) is None:
            closed = True
            break
        time.sleep(0.1)
    if closed and popup["title"].strip().lower() in ("data loss warning", "verse validation errors"):
        mark_verse_skip()
    return {"ok": True, "title": popup["title"], "pressed": int(n), "closed": closed,
            "open_popups": [p["title"] for p in list_popups()]}


# hwnd -> (title, looks so far, monotonic time of the next look). A known popup that
# did not match its rule, or stayed open after its button was pressed, is looked at
# again only after a growing pause: each look is a capture plus a per-pixel scan, and
# a stuck popup otherwise cost that every pass until someone closed it.
_retry: dict[int, tuple[str, int, float]] = {}
_RETRY_MAX_S = 60.0


def _back_off(hwnd: int, title: str, now: float) -> None:
    seen = _retry.get(hwnd)
    tries = seen[1] + 1 if seen is not None and seen[0] == title else 1
    _retry[hwnd] = (title, tries, now + min(_RETRY_MAX_S, _GUARD_POLL_S * 2**tries))


def answer_known_popups() -> list[dict[str, Any]]:
    """Press the rule's button on every known popup on screen. One pass; cheap when none."""
    done = []
    now = time.monotonic()
    on_screen = list_popups()
    for hwnd in set(_retry) - {p["hwnd"] for p in on_screen}:
        del _retry[hwnd]
    for popup in on_screen:
        title = popup["title"].strip().lower()
        if title not in KNOWN_POPUPS:
            continue
        seen = _retry.get(popup["hwnd"])
        if seen is not None and seen[0] == title and now < seen[2]:
            continue
        info = describe_popup(popup, save=False)
        if not info.get("known"):
            _back_off(popup["hwnd"], title, now)
            done.append({"title": popup["title"], "skipped": "layout did not match", "buttons": len(info["buttons"])})
            continue
        result = press_button(popup["hwnd"], info["known"]["press"])
        if not result.get("closed"):
            _back_off(popup["hwnd"], title, now)
        done.append({**result, "label": info["known"]["label"]})
    return done


# --------------------------------------------------------------------------- guard


_guard_started = False
_guard_lock = threading.Lock()
_GUARD_POLL_S = 1.5


def start_popup_guard() -> None:
    """Answer the known Verse popups whenever UEFN shows them (one daemon thread)."""
    global _guard_started
    if sys.platform != "win32":
        return
    with _guard_lock:
        if _guard_started:
            return
        _guard_started = True

    def _run() -> None:
        while True:
            try:
                for event in answer_known_popups():
                    _log_event(event)
            except Exception:
                pass
            time.sleep(_GUARD_POLL_S)

    threading.Thread(target=_run, name="uefn-popup-guard", daemon=True).start()


def _log_event(event: dict[str, Any]) -> None:
    try:
        from backend.store.repos import events as events_repo

        if event.get("skipped"):
            message = f"{event.get('title')}: not pressed ({event['skipped']})"
        else:
            message = f"{event.get('title')}: pressed {event.get('label') or event.get('pressed')}"
        now = time.time()
        events_repo.insert("uefn_popup", ts=now, source="popup-guard", message=message, payload=event)
        # A popup that stays stuck is logged again on each look; keep the log bounded.
        events_repo.trim("uefn_popup", older_than=now - 30 * 86400, keep=500)
    except Exception:
        pass


# --------------------------------------------------------------------------- no saving after a skip


def _skip_file() -> Path:
    from frontend.settings import default_app_data_dir

    return default_app_data_dir() / "uefn_verse_skip.json"


def _uefn_pids() -> set[int]:
    if sys.platform != "win32":
        return set()
    import ctypes
    from ctypes import wintypes

    from backend.tools.core.uefn_modal import _enum_uefn_windows

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    pids = set()
    for win in _enum_uefn_windows():
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(wintypes.HWND(int(win["hwnd"])), ctypes.byref(pid))
        if pid.value:
            pids.add(int(pid.value))
    return pids


def mark_verse_skip() -> None:
    """Record that UEFN opened past Verse errors: the loaded level must not be saved."""
    pids = sorted(_uefn_pids())
    try:
        path = _skip_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"at": time.time(), "uefn_pids": pids}), encoding="utf-8")
    except OSError:
        pass


def verse_skip_active() -> bool:
    """True while the UEFN that skipped its Verse errors still has that level loaded."""
    try:
        data = json.loads(_skip_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    pids = {int(p) for p in data.get("uefn_pids") or []}
    if pids and not (pids & _uefn_pids()):
        clear_verse_skip()  # that UEFN is gone; a fresh one loads the level from disk
        return False
    return True


def clear_verse_skip() -> None:
    try:
        os.remove(_skip_file())
    except OSError:
        pass


_RELOAD_LEVEL = """
world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
path = world.get_path_name().split(".")[0] if world else ""
# Closes the current level without saving and loads it from disk: its Verse devices
# find their classes again now that Verse builds.
result = {"level": path, "loaded": bool(path and level_sub.load_level(path))}
"""


def reload_level_after_clean_build() -> dict[str, Any] | None:
    """After a clean Verse build, reload a level that was opened past Verse errors.

    None when nothing was skipped. On success the save block is lifted.
    """
    if not verse_skip_active():
        return None
    try:
        from backend.bridge.client import send_command

        out = send_command("execute_python", {"code": _RELOAD_LEVEL}, timeout=120.0)
    except Exception as exc:
        return {"loaded": False, "error": str(exc)[:300]}
    result = out.get("result") if isinstance(out, dict) else None
    if isinstance(result, dict) and result.get("loaded"):
        clear_verse_skip()
        return result
    return {"loaded": False, "error": str((out or {}).get("stderr") or "load_level returned false")[:300]}


SKIP_SAVE_ERROR = (
    "Not saved: UEFN opened this level past Verse errors, so its Verse devices are broken in memory "
    "and saving now would break them for good. Fix Verse and build it clean (workspace_compile_verse); "
    "Ducky then reloads the map from disk and saving works again."
)
