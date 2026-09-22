"""Host-side UEFN window list, capture, click, and the private-version popup.

Slate widgets are invisible to UI Automation. These helpers only use the native
window title and a real pointer click (inject_pointer). Copy/Done positions are
cached per dialog size after the first successful read.
"""

from __future__ import annotations

import io
import json
import re
import time
from pathlib import Path
from typing import Any

_SUCCESS_TITLE = r"Private version has been created"
_CODE_RE = re.compile(r"\d{4}-\d{4}-\d{4}(?:\?v=\d+)?")
_WAIT_CAP_S = 300.0
_CLICK_CACHE = "uefn_publish_clicks.json"


def island_code_in(text: str) -> str:
    match = _CODE_RE.search(text or "")
    return match.group(0) if match else ""


def list_uefn_windows(title_regex: str = "") -> dict[str, Any]:
    from backend.tools.core.uefn_modal import _enum_uefn_windows

    try:
        pat = re.compile(title_regex, re.I) if (title_regex or "").strip() else None
    except re.error as exc:
        return {"ok": False, "error": f"bad title regex: {exc}", "windows": []}
    rows: list[dict[str, Any]] = []
    for win in _enum_uefn_windows():
        title = str(win.get("title") or "")
        if pat is not None and not pat.search(title):
            continue
        hwnd = int(win["hwnd"])
        rows.append(
            {
                "hwnd": hwnd,
                "title": title,
                "rect": _rect(hwnd),
                "owned": bool(win.get("owned")),
            }
        )
    return {"ok": True, "windows": rows}


def capture_uefn_window(*, hwnd: int = 0, title_regex: str = "") -> dict[str, Any]:
    win = _resolve(hwnd, title_regex)
    if win.get("ok") is False:
        return win
    rect = win.get("rect") or {}
    box = (
        int(rect.get("left") or 0),
        int(rect.get("top") or 0),
        int(rect.get("right") or 0),
        int(rect.get("bottom") or 0),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        return {"ok": False, "error": "window has no on-screen rect", "hwnd": win.get("hwnd")}
    try:
        from PIL import ImageGrab
    except Exception as exc:
        return {"ok": False, "error": f"PIL ImageGrab unavailable: {exc}"}
    try:
        image = ImageGrab.grab(bbox=box, all_screens=True)
    except TypeError:
        image = ImageGrab.grab(bbox=box)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    from frontend.ui_web.tool_captures import save_capture_for_agents

    saved = save_capture_for_agents(buf.getvalue(), prefix="uefn_window")
    return {
        "ok": True,
        "hwnd": win.get("hwnd"),
        "title": win.get("title") or "",
        "rect": rect,
        "path": str(saved.get("path") or ""),
        "capture_path": str(saved.get("capture_path") or saved.get("path") or ""),
        "media_url": saved.get("media_url") or "",
        "bytes": saved.get("bytes"),
    }


def click_uefn_window(
    hwnd: int,
    nx: float,
    ny: float,
    *,
    button: int = 0,
    dblclick: bool = False,
) -> dict[str, Any]:
    from frontend.window_view import inject_pointer

    hid = int(hwnd or 0)
    if hid <= 0:
        return {"ok": False, "error": "hwnd required"}
    kind = "dblclick" if dblclick else "down"
    inject_pointer(hid, kind, float(nx), float(ny), button=int(button))
    if not dblclick:
        inject_pointer(hid, "up", float(nx), float(ny), button=int(button))
    return {"ok": True, "hwnd": hid, "nx": float(nx), "ny": float(ny)}


def key_uefn_window(hwnd: int, key: str) -> dict[str, Any]:
    from frontend.window_view import inject_key

    hid = int(hwnd or 0)
    name = (key or "").strip()
    if hid <= 0 or not name:
        return {"ok": False, "error": "hwnd and key required"}
    inject_key(hid, name, down=True)
    inject_key(hid, name, down=False)
    return {"ok": True, "hwnd": hid, "key": name}


def wait_uefn_window(title_regex: str, timeout: float = 120.0) -> dict[str, Any]:
    pattern = (title_regex or "").strip()
    if not pattern:
        return {"ok": False, "error": "title_regex required"}
    cap = min(max(float(timeout or 0), 1.0), _WAIT_CAP_S)
    deadline = time.time() + cap
    last: dict[str, Any] = {"windows": []}
    while time.time() < deadline:
        last = list_uefn_windows(pattern)
        if last.get("ok") is False:
            return last
        wins = last.get("windows") or []
        if wins:
            hit = wins[0]
            return {"ok": True, **hit}
        time.sleep(0.5)
    return {"ok": False, "error": "timed out waiting for window", "title_regex": pattern}


def publish_private_version(
    *,
    timeout: float = 180.0,
    copy_nx: float | None = None,
    copy_ny: float | None = None,
    done_nx: float | None = None,
    done_ny: float | None = None,
) -> dict[str, Any]:
    """Wait for the private-version popup, capture it, click Copy when we can.

    Cached Copy/OK spots are used when the dialog is the same size as the
    last success. done_nx/done_ny is the OK button. Passing copy_nx/copy_ny
    clicks those spots and, if the clipboard then holds an island code,
    stores them for next time. OK must be pressed or a memory calculation
    stays blocked on this popup.
    """
    found = wait_uefn_window(_SUCCESS_TITLE, timeout=timeout)
    if not found.get("ok"):
        return found
    shot = capture_uefn_window(hwnd=int(found.get("hwnd") or 0))
    out: dict[str, Any] = {
        "ok": True,
        "hwnd": found.get("hwnd"),
        "title": found.get("title") or "",
        "rect": found.get("rect") or shot.get("rect") or {},
        "path": shot.get("path") or "",
        "media_url": shot.get("media_url") or "",
        "code": "",
        "clicked_copy": False,
    }
    if shot.get("ok") is False:
        out["capture_error"] = shot.get("error") or "capture failed"
    rect = out["rect"] if isinstance(out["rect"], dict) else {}
    cached = _load_clicks()
    nx, ny = _pick_copy(rect, cached, copy_nx, copy_ny)
    if nx is None or ny is None:
        out["text"] = "Private version popup is up. No cached Copy position yet."
        return out
    from frontend.ui_web.win_clipboard import get_clipboard_text, set_clipboard_text

    # Drop whatever was on the clipboard so a failed Copy cannot reuse an old code.
    set_clipboard_text("")
    click_uefn_window(int(out["hwnd"] or 0), nx, ny)
    time.sleep(0.3)

    code = island_code_in(get_clipboard_text())
    if not code:
        out["text"] = "Clicked Copy but the clipboard had no island code."
        return out
    set_clipboard_text(code)
    done = _pick_done(rect, cached, done_nx, done_ny)
    if done[0] is not None and done[1] is not None:
        click_uefn_window(int(out["hwnd"] or 0), done[0], done[1])
    if copy_nx is not None and copy_ny is not None:
        _save_clicks(rect, (float(copy_nx), float(copy_ny)), done)
    out["code"] = code
    out["clicked_copy"] = True
    out["text"] = f"Private version code: {code}"
    return out


def _rect(hwnd: int) -> dict[str, int]:
    from frontend.window_view import _window_box

    box = _window_box(int(hwnd))
    if not box:
        return {}
    left, top, right, bottom = box
    return {
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "width": right - left,
        "height": bottom - top,
    }


def _resolve(hwnd: int, title_regex: str) -> dict[str, Any]:
    if int(hwnd or 0) > 0:
        listed = list_uefn_windows()
        for win in listed.get("windows") or []:
            if int(win.get("hwnd") or 0) == int(hwnd):
                return {"ok": True, **win}
        return {"ok": False, "error": f"UEFN window {hwnd} not found"}
    if (title_regex or "").strip():
        listed = list_uefn_windows(title_regex)
        if listed.get("ok") is False:
            return listed
        wins = listed.get("windows") or []
        if not wins:
            return {"ok": False, "error": "no UEFN window matched", "title_regex": title_regex}
        return {"ok": True, **wins[0]}
    return {"ok": False, "error": "hwnd or title_regex required"}


def _click_path() -> Path:
    from frontend.app_paths import resolve_app_data_dir

    return resolve_app_data_dir() / _CLICK_CACHE


def _load_clicks() -> dict[str, Any]:
    path = _click_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _same_size(rect: dict[str, Any], cached: dict[str, Any]) -> bool:
    try:
        return abs(int(rect.get("width") or 0) - int(cached.get("w") or 0)) <= 8 and abs(
            int(rect.get("height") or 0) - int(cached.get("h") or 0)
        ) <= 8
    except (TypeError, ValueError):
        return False


def _pair(raw: Any) -> tuple[float, float] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    try:
        return float(raw[0]), float(raw[1])
    except (TypeError, ValueError):
        return None


def _pick_copy(
    rect: dict[str, Any],
    cached: dict[str, Any],
    nx: float | None,
    ny: float | None,
) -> tuple[float | None, float | None]:
    if nx is not None and ny is not None:
        return float(nx), float(ny)
    if _same_size(rect, cached):
        pair = _pair(cached.get("copy"))
        if pair:
            return pair
    return None, None


def _pick_done(
    rect: dict[str, Any],
    cached: dict[str, Any],
    nx: float | None,
    ny: float | None,
) -> tuple[float | None, float | None]:
    if nx is not None and ny is not None:
        return float(nx), float(ny)
    if _same_size(rect, cached):
        pair = _pair(cached.get("done"))
        if pair:
            return pair
    return None, None


def _save_clicks(
    rect: dict[str, Any],
    copy: tuple[float, float],
    done: tuple[float | None, float | None],
) -> None:
    row: dict[str, Any] = {
        "w": int(rect.get("width") or 0),
        "h": int(rect.get("height") or 0),
        "copy": [copy[0], copy[1]],
    }
    if done[0] is not None and done[1] is not None:
        row["done"] = [done[0], done[1]]
    path = _click_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(row), encoding="utf-8")
    except OSError:
        return
