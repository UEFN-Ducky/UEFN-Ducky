"""OS-level Focus windows — tab groups (each group is one OS window with multiple tabs)."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

_lock = threading.Lock()
_closing_windows: set[int] = set()
_main_window: Any = None
_base_url: str = ""
_api: Any = None
_connection_mode: str = "offline"


@dataclass
class _FocusGroup:
    window: Any
    tabs: dict[str, str] = field(default_factory=dict)
    layout: dict[str, Any] | None = None
    # Opaque window identity for the tab registry. NEVER a tab id: a focus window
    # can host many tabs, so borrowing its birth-tab id misattributes ownership.
    wid: str = field(default_factory=lambda: f"focus-{uuid.uuid4().hex[:12]}")


_focus_groups: list[_FocusGroup] = []


def configure(*, api: Any, base_url: str, main_window: Any) -> None:
    global _api, _base_url, _main_window
    _api = api
    _base_url = base_url.rstrip("/")
    _main_window = main_window


def set_connection_mode(mode: str) -> None:
    global _connection_mode
    _connection_mode = mode


def _focus_url(focus_id: str, title: str, wid: str) -> str:
    parsed = urlsplit(_base_url)
    query = f"focus={quote(focus_id, safe='')}&title={quote(title, safe='')}&wid={quote(wid, safe='')}"
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))


def _drop_registry_window(wid: str) -> None:
    """Deregister a closing window IMMEDIATELY — heartbeat expiry is crash-only.
    A dead window left in the registry ghost-owns its tabs for up to 30s, during
    which every open request raises a window that no longer exists."""
    try:
        from frontend.ui_web import tab_registry

        tab_registry.drop_window(wid)
    except Exception:
        pass


def _apply_icon(window: Any) -> None:
    try:
        from frontend.ui_web.win_frameless import refresh_window_chrome, set_window_icon_hwnd

        for _ in range(20):
            if refresh_window_chrome(window) and set_window_icon_hwnd(window, mode=_connection_mode):
                return
            time.sleep(0.05)
    except Exception:
        pass


def _bring_to_front(window: Any) -> None:
    try:
        window.show()
    except Exception:
        pass
    try:
        window.restore()
    except Exception:
        pass
    _force_foreground(window)


def _force_foreground(window: Any) -> None:
    """Best-effort raise above other applications (Windows)."""
    import sys

    if sys.platform != "win32":
        return
    try:
        from frontend.ui_web.win_frameless import force_window_foreground

        force_window_foreground(window)
    except Exception:
        pass


def _push_focus_event(window: Any, handler: str, focus_id: str, title: str) -> None:
    import json

    from frontend.ui_web.ui_dispatch import schedule_evaluate_js

    try:
        payload = json.dumps({"focus_id": focus_id, "title": title}, ensure_ascii=False)
        schedule_evaluate_js(window, f"window.{handler} && window.{handler}({payload})")
    except Exception:
        pass


def _push_focus_layout_restore(window: Any, layout: dict[str, Any]) -> None:
    import json

    from frontend.ui_web.ui_dispatch import schedule_evaluate_js

    try:
        payload = json.dumps({"layout": layout}, ensure_ascii=False)
        schedule_evaluate_js(window, f"window.__uefnFocusRestoreLayout && window.__uefnFocusRestoreLayout({payload})")
    except Exception:
        pass


def report_focus_window_layout(birth_tab_id: str, layout: dict[str, Any]) -> None:
    """Persist split layout for a focus OS window (merged into editor.json on save)."""
    with _lock:
        group = None
        for g in _focus_groups:
            tab_ids = list(g.tabs.keys())
            if not tab_ids:
                continue
            birth = tab_ids[0]
            if birth == birth_tab_id or birth_tab_id in g.tabs:
                group = g
                break
        if group is None:
            return
        group.layout = layout if isinstance(layout, dict) else None


def _primary_group() -> _FocusGroup | None:
    return _focus_groups[0] if _focus_groups else None


def _find_group_for_tab(focus_id: str) -> _FocusGroup | None:
    for group in _focus_groups:
        if focus_id in group.tabs:
            return group
    return None


def _find_group_for_window(window: Any) -> _FocusGroup | None:
    for group in _focus_groups:
        if group.window is window:
            return group
    return None


def window_for_wid(wid: str) -> Any | None:
    """Focus window by its opaque registry wid, or None (used by browser panes)."""
    w = (wid or "").strip()
    if not w:
        return None
    with _lock:
        group = next((g for g in _focus_groups if g.wid == w), None)
        return group.window if group is not None else None


def raise_window(window_id: str) -> None:
    """Raise a focus window by its opaque registry wid (NOT a tab id)."""
    wid = (window_id or "").strip()
    if not wid:
        return
    with _lock:
        group = next((g for g in _focus_groups if g.wid == wid), None)
    if group is not None:
        _bring_to_front(group.window)


def raise_focus_window(focus_id: str) -> None:
    focus_id = (focus_id or "").strip()
    if not focus_id:
        return
    with _lock:
        group = _find_group_for_tab(focus_id)
    if group is None:
        return
    title = group.tabs.get(focus_id, "")
    _bring_to_front(group.window)
    _push_focus_event(group.window, "__uefnFocusTabActivate", focus_id, title)


def _push_appearance_to_window(window: Any) -> None:
    """Sync saved appearance into a focus window as soon as its React bundle is listening."""
    import json

    from frontend.ui_web.ui_dispatch import schedule_evaluate_js

    if _api is None or not hasattr(_api, "get_appearance"):
        return
    try:
        appearance = _api.get_appearance()
        payload = json.dumps({"type": "appearance_changed", "appearance": appearance}, ensure_ascii=False)
        schedule_evaluate_js(
            window,
            f"window.__uefnPanelPush && window.__uefnPanelPush({payload})",
        )
    except Exception:
        pass


def _signal_api_ready(window: Any) -> None:
    """Focus windows may load React before pywebview injects the API."""
    from frontend.ui_web.ui_dispatch import schedule_evaluate_js

    script = "window.dispatchEvent(new Event('pywebviewready'));"
    for delay in (0.0, 0.05, 0.15, 0.35, 0.75):
        if delay:
            time.sleep(delay)
        schedule_evaluate_js(window, script)
    _push_appearance_to_window(window)


def _notify_main_window(handler: str, focus_id: str, title: str) -> None:
    import json

    from frontend.ui_web.ui_dispatch import schedule_evaluate_js

    if _main_window is None:
        return
    try:
        payload = json.dumps({"focus_id": focus_id, "title": title}, ensure_ascii=False)
        schedule_evaluate_js(
            _main_window,
            f"window.{handler} && window.{handler}({payload})",
        )
    except Exception:
        pass


def notify_focus_tab_active(focus_id: str, title: str) -> None:
    """Tell the main window which tab is active in a focus window (sidebar sync)."""
    focus_id = (focus_id or "").strip()
    if not focus_id:
        return
    _notify_main_window("__uefnFocusTabActive", focus_id, title)


def _return_tabs_to_main(tabs: dict[str, str]) -> None:
    """Reopen a focus window's file/chat tabs in the main window so closing the
    window (or its last tab) never loses them. Terminals are window-bound sessions
    that cannot be handed off — they close with the window."""
    for focus_id, title in tabs.items():
        if focus_id.startswith("terminal:"):
            continue
        _notify_main_window("__uefnFocusTabReturn", focus_id, title)


def _iter_focus_windows() -> list[Any]:
    out: list[Any] = []
    with _lock:
        seen: set[int] = set()
        for group in _focus_groups:
            wid = id(group.window)
            if wid not in _closing_windows and wid not in seen:
                out.append(group.window)
                seen.add(wid)
    return out


def all_windows() -> list[Any]:
    out: list[Any] = []
    if _main_window is not None:
        out.append(_main_window)
    out.extend(_iter_focus_windows())
    return out


def _mark_closing(window: Any) -> None:
    with _lock:
        _closing_windows.add(id(window))


def _unmark_closing(window: Any) -> None:
    with _lock:
        _closing_windows.discard(id(window))


def window_for_focus_id(focus_id: str) -> Any | None:
    with _lock:
        group = _find_group_for_tab(focus_id)
        return group.window if group is not None else None


def _destroy_window(window: Any) -> None:
    from frontend.ui_web.ui_dispatch import drop_window, schedule_call

    def op() -> None:
        try:
            window.destroy()
        except Exception:
            pass
        finally:
            drop_window(window)
            _unmark_closing(window)

    schedule_call(op)


def _create_focus_window(focus_id: str, title: str, display_title: str, wid: str) -> Any:
    import webview

    from frontend.ui_web import window_bounds

    url = _focus_url(focus_id, title, wid)
    saved = window_bounds.get_bounds(f"focus:{focus_id}")
    window = webview.create_window(
        display_title,
        url=url,
        width=saved["width"] if saved else 720,
        height=saved["height"] if saved else 520,
        x=saved["x"] if saved else None,
        y=saved["y"] if saved else None,
        min_size=(360, 280),
        resizable=True,
        frameless=True,
        easy_drag=False,
        background_color="#0a0a0a",
        js_api=_api,
    )
    window_bounds.track(window, f"focus:{focus_id}")
    return window


def _wire_group_window(group: _FocusGroup) -> None:
    window = group.window

    def _on_closing() -> bool:
        _mark_closing(window)
        with _lock:
            if group in _focus_groups:
                _focus_groups.remove(group)
            returning = dict(group.tabs)
            group.tabs.clear()
        # Closing the whole OS window must not lose its tabs: hand every file/chat
        # tab back to the main window (same path as closing a single tab). Done
        # outside the lock — it schedules JS on the main window.
        _return_tabs_to_main(returning)
        _drop_registry_window(group.wid)
        return True

    def _on_shown() -> None:
        threading.Thread(target=_apply_icon, args=(window,), daemon=True, name="focus-window-icon").start()
        threading.Thread(target=_signal_api_ready, args=(window,), daemon=True, name="focus-api-ready").start()

    window.events.closing += _on_closing
    window.events.shown += _on_shown


def _create_group_with_tab(focus_id: str, title: str) -> _FocusGroup:
    display_title = f"{(title or focus_id).strip()} — UEFN Ducky"
    wid = f"focus-{uuid.uuid4().hex[:12]}"
    window = _create_focus_window(focus_id, title, display_title, wid)
    group = _FocusGroup(window=window, tabs={focus_id: title}, wid=wid)
    _wire_group_window(group)
    with _lock:
        _focus_groups.append(group)
    return group


def _add_tab_to_group(group: _FocusGroup, focus_id: str, title: str) -> None:
    if focus_id in group.tabs:
        _bring_to_front(group.window)
        _push_focus_event(group.window, "__uefnFocusTabActivate", focus_id, title)
        return
    group.tabs[focus_id] = title
    _push_focus_event(group.window, "__uefnFocusTabOpen", focus_id, title)
    _bring_to_front(group.window)


def _destroy_group(group: _FocusGroup) -> None:
    _mark_closing(group.window)
    try:
        from frontend.ui_web.win_frameless import (
            _hwnd_from_pywebview,
            release_native_chrome_subclass,
        )

        hwnd = _hwnd_from_pywebview(group.window)
        if hwnd:
            release_native_chrome_subclass(hwnd)
    except Exception:
        pass
    with _lock:
        if group in _focus_groups:
            _focus_groups.remove(group)
        group.tabs.clear()
    _drop_registry_window(group.wid)
    _destroy_window(group.window)


def _cursor_screen_point() -> tuple[int, int] | None:
    """Live cursor in the SAME logical pixels as window bounds and DOM screenX/Y.

    GetCursorPos answers in physical pixels; on a scaled display that landed far from
    the window it was meant to hit, so the hit test kept missing.
    """
    import sys

    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        pt = POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return None
        scale = 1.0
        if _main_window is not None:
            from frontend.ui_web.win_frameless import get_window_scale

            scale = get_window_scale(_main_window) or 1.0
        return int(pt.x / scale), int(pt.y / scale)
    except Exception:
        pass
    return None


def _window_contains(window: Any, screen_x: int, screen_y: int) -> bool:
    from frontend.ui_web.win_frameless import read_window_bounds

    try:
        bounds = read_window_bounds(window)
        x = int(bounds.get("x", 0))
        y = int(bounds.get("y", 0))
        w = int(bounds.get("width", 0))
        h = int(bounds.get("height", 0))
    except Exception:
        return False
    if w <= 0 or h <= 0:
        return False
    return x <= screen_x < x + w and y <= screen_y < y + h


def _find_group_at_point(screen_x: int, screen_y: int) -> _FocusGroup | None:
    """Return the focus group whose window contains the screen point (topmost wins)."""
    with _lock:
        groups = list(_focus_groups)
    for group in reversed(groups):
        if _window_contains(group.window, screen_x, screen_y):
            return group
    return None


def adopt_tab_into_focus_window(focus_id: str, title: str, window: Any) -> None:
    """Add a tab to the focus window that received the drop (no hit-test)."""
    focus_id = (focus_id or "").strip()
    if not focus_id:
        raise ValueError("focus_id is required")
    group = _find_group_for_window(window)
    if group is None:
        raise ValueError("not a focus window")
    _add_tab_to_group(group, focus_id, title)


def drop_hit_points(
    screen_x: int, screen_y: int, cursor: tuple[int, int] | None
) -> list[tuple[int, int]]:
    """Candidate release points, best first: the drag's drop coords, then the live cursor.

    The drop coords are preferred — at dragend the cursor can already have drifted. But
    WebView2 zeroes those coords whenever the drop landed outside the source window (and
    the last in-window dragover point is stale), so the cursor is kept as a fallback
    rather than trusted first.
    """
    points: list[tuple[int, int]] = []
    x, y = int(screen_x), int(screen_y)
    if x or y:
        points.append((x, y))
    if cursor is not None:
        point = (int(cursor[0]), int(cursor[1]))
        if point not in points:
            points.append(point)
    return points


def open_focus_window_at_point(focus_id: str, title: str, screen_x: int, screen_y: int) -> bool:
    """Add the tab to the focus window under the drop point, else tear off a new window.

    Returns False when the tab was let go back inside the main window (drag cancelled),
    so the caller keeps it where it is instead of losing it to a window nobody opened.
    """
    focus_id = (focus_id or "").strip()
    if not focus_id:
        raise ValueError("focus_id is required")

    with _lock:
        existing = _find_group_for_tab(focus_id)
    if existing is not None:
        _bring_to_front(existing.window)
        _push_focus_event(existing.window, "__uefnFocusTabActivate", focus_id, title)
        return True

    points = drop_hit_points(screen_x, screen_y, _cursor_screen_point())
    for hit_x, hit_y in points:
        group = _find_group_at_point(hit_x, hit_y)
        if group is not None:
            _add_tab_to_group(group, focus_id, title)
            return True

    if _main_window is not None and any(_window_contains(_main_window, x, y) for x, y in points):
        return False

    group = _create_group_with_tab(focus_id, title)
    _bring_to_front(group.window)
    return True


def open_focus_window(focus_id: str, title: str, *, solo: bool = False) -> None:
    """Open a tab in a focus group. solo=True starts a new OS window group; solo=False adds to the primary group."""
    focus_id = (focus_id or "").strip()
    if not focus_id:
        raise ValueError("focus_id is required")

    with _lock:
        existing = _find_group_for_tab(focus_id)
    if existing is not None:
        _bring_to_front(existing.window)
        _push_focus_event(existing.window, "__uefnFocusTabActivate", focus_id, title)
        return

    if solo:
        group = _create_group_with_tab(focus_id, title)
        _bring_to_front(group.window)
        return

    with _lock:
        primary = _primary_group()
    if primary is None:
        group = _create_group_with_tab(focus_id, title)
        _bring_to_front(group.window)
        return

    _add_tab_to_group(primary, focus_id, title)


def open_focus_window_group(tabs: list[dict[str, Any]]) -> None:
    """Move a batch of tabs into ONE focus group (sidebar-only hand-off).

    Tabs already living in some focus group stay where they are; the rest all
    land in the same new (or first reused) group so they travel together.
    """
    cleaned: list[tuple[str, str]] = []
    for raw in tabs or []:
        if not isinstance(raw, dict):
            continue
        focus_id = str(raw.get("focus_id") or "").strip()
        if not focus_id:
            continue
        cleaned.append((focus_id, str(raw.get("title") or "")))
    if not cleaned:
        return

    group: _FocusGroup | None = None
    for focus_id, title in cleaned:
        with _lock:
            existing = _find_group_for_tab(focus_id)
        if existing is not None:
            if group is None:
                group = existing
            continue
        if group is None:
            group = _create_group_with_tab(focus_id, title)
        else:
            _add_tab_to_group(group, focus_id, title)

    if group is not None:
        _bring_to_front(group.window)


def dock_focus_window_beside_main() -> None:
    """Position all focus group windows beside the compact main window."""
    if _main_window is None:
        return

    try:
        main_x = int(_main_window.x)
        main_y = int(_main_window.y)
        main_w = max(1, int(_main_window.width))
        main_h = int(_main_window.height)
    except Exception:
        return

    saved_bounds = None
    if _api is not None and hasattr(_api, "get_sidebar_only_saved_bounds"):
        try:
            saved_bounds = _api.get_sidebar_only_saved_bounds()
        except Exception:
            saved_bounds = None

    default_focus_w = 720
    if saved_bounds:
        try:
            saved_w = int(saved_bounds.get("width", 0))
            if saved_w > main_w:
                default_focus_w = max(360, saved_w - main_w)
        except Exception:
            pass

    with _lock:
        groups = list(_focus_groups)

    if not groups:
        return

    try:
        from frontend.ui_web.win_frameless import set_window_bounds_hwnd
    except Exception:
        return

    count = len(groups)
    gap = 8
    total_gap = gap * max(0, count - 1)
    each_h = max(280, (main_h - total_gap) // count)
    focus_w = default_focus_w

    y = main_y
    for group in groups:
        h = each_h if count > 1 else main_h
        try:
            set_window_bounds_hwnd(group.window, main_x + main_w, y, focus_w, h)
        except Exception:
            pass
        y += h + gap


def return_tab_to_main(focus_id: str, title: str) -> bool:
    """Close a file/chat tab in a focus window and reopen it in the main window.

    Returns False only when the tab can't be handed off (terminals are window-
    bound sessions; unknown focus_id), so the caller closes it locally instead.
    """
    focus_id = (focus_id or "").strip()
    if not focus_id or focus_id.startswith("terminal:"):
        return False
    with _lock:
        group = _find_group_for_tab(focus_id)
        if group is None:
            return False
    _notify_main_window("__uefnFocusTabReturn", focus_id, title)
    close_focus_window(focus_id)
    return True


def close_focus_window(focus_id: str) -> None:
    focus_id = (focus_id or "").strip()
    if not focus_id:
        return

    with _lock:
        group = _find_group_for_tab(focus_id)
        if group is None:
            return
        title = group.tabs.pop(focus_id, "")
        remaining = len(group.tabs)

    if remaining == 0:
        _destroy_group(group)
    else:
        _push_focus_event(group.window, "__uefnFocusTabClose", focus_id, title)


def list_focus_window_ids() -> list[str]:
    with _lock:
        ids: list[str] = []
        for group in _focus_groups:
            ids.extend(group.tabs.keys())
        return ids


def export_snapshot() -> list[dict[str, Any]]:
    """Live focus-window groups for editor workspace persistence."""
    with _lock:
        groups = list(_focus_groups)
    out: list[dict[str, Any]] = []
    for group in groups:
        tab_ids = list(group.tabs.keys())
        if not tab_ids:
            continue
        birth = tab_ids[0]
        out.append(
            {
                "birthTabId": birth,
                "tabIds": tab_ids,
                "titles": dict(group.tabs),
                **({"layout": group.layout} if isinstance(group.layout, dict) else {}),
            }
        )
    return out


def restore_groups(groups: list[dict[str, Any]]) -> None:
    """Reopen focus OS windows from a saved snapshot (app restart)."""
    for raw in groups:
        if not isinstance(raw, dict):
            continue
        tab_ids_raw = raw.get("tabIds")
        if not isinstance(tab_ids_raw, list):
            continue
        tab_ids = [t.strip() for t in tab_ids_raw if isinstance(t, str) and t.strip()]
        if not tab_ids:
            continue
        titles_raw = raw.get("titles")
        titles: dict[str, str] = titles_raw if isinstance(titles_raw, dict) else {}
        birth_raw = raw.get("birthTabId")
        birth = birth_raw.strip() if isinstance(birth_raw, str) and birth_raw.strip() in tab_ids else tab_ids[0]
        ordered = [birth] + [t for t in tab_ids if t != birth]

        group: _FocusGroup | None = None
        saved_layout = raw.get("layout")
        layout = saved_layout if isinstance(saved_layout, dict) else None
        for tab_id in ordered:
            title = str(titles.get(tab_id) or tab_id.split(":", 1)[-1])
            if group is None:
                group = _create_group_with_tab(tab_id, title)
                _bring_to_front(group.window)
            else:
                _add_tab_to_group(group, tab_id, title)
        if group is not None and layout is not None:
            group.layout = layout
            _push_focus_layout_restore(group.window, layout)


def close_all_focus_windows() -> None:
    with _lock:
        groups = list(_focus_groups)
        _focus_groups.clear()

    for group in groups:
        group.tabs.clear()
        _mark_closing(group.window)
        _drop_registry_window(group.wid)
        _destroy_window(group.window)


def close_window(window: Any) -> None:
    if window is _main_window:
        return

    with _lock:
        group = _find_group_for_window(window)
        if group is None:
            return
        if group in _focus_groups:
            _focus_groups.remove(group)
        group.tabs.clear()

    _mark_closing(window)
    _drop_registry_window(group.wid)
    _destroy_window(window)


def is_focus_window(window: Any) -> bool:
    with _lock:
        return _find_group_for_window(window) is not None


def is_main_window(window: Any) -> bool:
    return window is not None and window is _main_window
