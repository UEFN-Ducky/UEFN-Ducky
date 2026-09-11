"""Reload WebView2 after a renderer/GPU death so the panel is not stuck black.

WebView2 paints ``WEBVIEW2_DEFAULT_BACKGROUND_COLOR`` (#0a0a0a) when the
renderer dies. Without ``ProcessFailed`` → Reload the window stays blank until
the user kills the EXE. Overlay browser panes that die while Visible sit on
top of the app and look the same.
"""

from __future__ import annotations

import time
from typing import Any, Callable

_DEBOUNCE_S = 3.0
_last_recover: dict[int, float] = {}
_attached: set[int] = set()


def recover_core_webview(core: Any, *, reason: str = "") -> dict[str, Any]:
    """Reload (or re-Navigate) a live CoreWebView2. Debounced per instance."""
    if core is None:
        return {"ok": False, "error": "no core"}
    key = id(core)
    now = time.monotonic()
    if now - _last_recover.get(key, 0.0) < _DEBOUNCE_S:
        return {"ok": True, "action": "debounced"}
    _last_recover[key] = now
    try:
        from frontend.error_log import record_error

        record_error("webview", f"WebView2 process failed ({reason or 'unknown'}); reloading")
    except Exception:
        pass
    try:
        core.Reload()
        return {"ok": True, "action": "reload"}
    except Exception as exc:
        try:
            src = str(getattr(core, "Source", "") or "")
            if src:
                core.Navigate(src)
                return {"ok": True, "action": "navigate"}
        except Exception:
            pass
        return {"ok": False, "error": str(exc)}


def attach_process_failed(
    core: Any,
    *,
    label: str = "main",
    on_fail: Callable[[str], None] | None = None,
) -> Any | None:
    """Subscribe ``ProcessFailed`` once. Keep the returned handler referenced."""
    if core is None:
        return None
    key = id(core)
    if key in _attached:
        return None

    def _on_fail(_sender: Any, event: Any) -> None:
        kind = ""
        try:
            kind = str(getattr(event, "ProcessFailedKind", "") or "")
        except Exception:
            pass
        reason = f"{label}:{kind}" if kind else label
        # Overlay panes: hide first so a dead control cannot cover the app.
        if on_fail is not None:
            try:
                on_fail(reason)
            except Exception:
                pass
        recover_core_webview(core, reason=reason)

    try:
        core.ProcessFailed += _on_fail
        _attached.add(key)
    except Exception:
        return None
    return _on_fail


def nudge_webview_visible(window: Any) -> None:
    """After hide-to-tray, make the WinForms WebView2 paint again.

    Occlusion can drop the compositor; a black DefaultBackgroundColor pane is
    left until Visible/Refresh. Does not Reload (that remounts React).
    """
    native = getattr(window, "native", None)
    ctrl = getattr(native, "webview", None) if native is not None else None
    if ctrl is None:
        return

    def _go() -> None:
        try:
            ctrl.Visible = True
        except Exception:
            pass
        try:
            ctrl.Refresh()
        except Exception:
            pass

    try:
        from frontend.ui_web.win_frameless import _run_on_form_ui

        _run_on_form_ui(native, _go)
    except Exception:
        try:
            _go()
        except Exception:
            pass
