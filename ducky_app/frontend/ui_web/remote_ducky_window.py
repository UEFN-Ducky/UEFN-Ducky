"""A window of this app that shows another UEFN Ducky of the account: its whole UI,
chats included, run peer to peer (web ``remote/directTransport.ts``, bridge ``ducky``).

The page loads this app's own bundle under ``/remote-ducky/`` (its Service Worker
scope stays there), with no JS bridge to this Ducky: every panel call goes to the
other Ducky over the link. Only the handshake comes back here
(``ducky_link_rtc_connect``, sealed through the site by :mod:`frontend.ducky_link`).
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Any
from urllib.parse import quote, urlencode

_open: dict[str, Any] = {}
_lock = threading.Lock()


def remote_ducky_url(base_url: str, key_id: str, name: str, chat_id: str = "") -> str:
    """``chat``: one of that Ducky's chats to open once the link is up."""
    from frontend.ui_web.panel_httpd import REMOTE_DUCKY_DIR

    params = {"direct": "ducky", "to": key_id, "name": name}
    if chat_id:
        params["chat"] = chat_id
    query = urlencode(params, quote_via=quote)
    return f"{base_url.rstrip('/')}/{REMOTE_DUCKY_DIR}?{query}"


def window_title(name: str) -> str:
    return f"{name} · UEFN Ducky"


def open_remote_ducky(key_id: str, name: str, chat_id: str = "") -> None:
    """Open (or bring forward) the window for UEFN Ducky ``key_id``, at ``chat_id`` if given."""
    import webview

    from frontend.ui_web.panel_httpd import panel_ui_http_url

    url = remote_ducky_url(panel_ui_http_url(), key_id, name, chat_id)
    with _lock:
        window = _open.get(key_id)
    if window is not None:
        try:
            if chat_id:
                window.load_url(url)  # the page reconnects and opens that chat
            window.restore()
            window.show()
            return
        except Exception:
            with _lock:
                _open.pop(key_id, None)
    # Frameless like a focus window: the page draws its own title bar (RemoteDuckyBar),
    # whose buttons reach this window through win_frameless's "uefnWindowChrome".
    # Off Windows there is no win_frameless, so the window keeps its native frame.
    windows = sys.platform == "win32"
    window = webview.create_window(
        window_title(name),
        url=url,
        width=1280,
        height=820,
        min_size=(520, 380),
        resizable=True,
        frameless=windows,
        easy_drag=False,
        background_color="#0a0a0a",
    )
    with _lock:
        _open[key_id] = window

    def _shown() -> None:
        if not windows:
            return
        threading.Thread(target=_apply_chrome, args=(window,), daemon=True, name="remote-ducky-icon").start()

    def _closed() -> None:
        with _lock:
            if _open.get(key_id) is window:
                _open.pop(key_id, None)
        try:
            from frontend.ui_web.ui_dispatch import drop_window

            drop_window(window)
        except Exception:
            pass

    try:
        window.events.shown += _shown
        window.events.closed += _closed
    except Exception:
        pass


def _apply_chrome(window: Any) -> None:
    """The app's icon and edge-to-edge frame, once the window has a handle."""
    try:
        from frontend.ui_web.win_frameless import refresh_window_chrome, set_window_icon_hwnd

        for _ in range(20):
            if refresh_window_chrome(window) and set_window_icon_hwnd(window):
                return
            time.sleep(0.05)
    except Exception:
        pass
