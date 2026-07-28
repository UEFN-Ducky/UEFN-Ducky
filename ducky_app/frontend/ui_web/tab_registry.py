"""Cross-window tab ownership — ONE tab per file/chat across main + focus windows.

Ownership is derived, not stored: each window reports its open tabs (debounced,
self-healing on restarts); the owner of a tab is whichever window currently reports it.
"""

from __future__ import annotations

import threading
import time

# Windows heartbeat their reports every 10s; entries older than this are dead
# windows (closed/crashed) and must never be treated as tab owners.
_STALE_S = 30.0

_lock = threading.Lock()
_window_tabs: dict[str, tuple[float, set[str]]] = {}


def report_open_tabs(window_id: str, tab_ids: list[str]) -> None:
    wid = (window_id or "main").strip() or "main"
    with _lock:
        _window_tabs[wid] = (time.time(), {t for t in tab_ids if isinstance(t, str) and t})


def find_tab_owner(tab_id: str, exclude_window: str = "") -> str:
    """LIVE window currently holding tab_id, excluding the asking window ('' if none)."""
    now = time.time()
    with _lock:
        for wid, (reported_at, tabs) in list(_window_tabs.items()):
            if now - reported_at > _STALE_S:
                del _window_tabs[wid]
                continue
            if wid != exclude_window and tab_id in tabs:
                return wid
    return ""


def drop_window(window_id: str) -> None:
    with _lock:
        _window_tabs.pop(window_id, None)
