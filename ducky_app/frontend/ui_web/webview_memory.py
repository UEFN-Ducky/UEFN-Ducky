"""Best-effort WebView2 memory targets; never suspend scripts. The one reload: a hidden,
idle window whose WebView2 grew past TRIM_GB (``trim_if_bloated``)."""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any

_log = logging.getLogger(__name__)


def enabled() -> bool:
    return sys.platform == "win32" and os.environ.get("UEFN_DUCKY_LOW_MEMORY", "1") != "0"


def update_control(control: Any, *, active: bool) -> bool:
    """Called on the WinForms UI thread. Old SDKs and disposed controls are safe."""
    if not enabled():
        return False
    try:
        core = control.CoreWebView2
        current = core.MemoryUsageTargetLevel
        # Use the enum supplied by the loaded SDK; importing a newer assembly is
        # unnecessary and would make older packaged runtimes fail at startup.
        target = getattr(type(current), "Normal" if active else "Low")
        if current != target:
            core.MemoryUsageTargetLevel = target
        return True
    except Exception:
        return False


# A 12.6 h session on 1.2.447 grew WebView2 from 0 to 2.5 GB before Ducky closed by itself, and
# webview_browser_exit_oom hit one PC 4 times (Oct 28 2026). Idle WebView2 sits under 0.5 GB.
TRIM_GB = 2.0
TRIM_EVERY_S = 30 * 60
_last_trim = {"at": 0.0}


def should_trim(beat: dict[str, Any], *, active: bool, now: float, last_at: float) -> bool:
    """Reload only when nobody is looking and nothing runs: hidden or minimized, no agent turn,
    WebView2 past TRIM_GB, and not within TRIM_EVERY_S of the last time."""
    try:
        webview_gb = float(beat.get("webview_gb") or 0.0)
    except (TypeError, ValueError):
        return False
    return (
        not active
        and int(beat.get("agents") or 0) == 0
        and webview_gb >= TRIM_GB
        and now - last_at >= TRIM_EVERY_S
    )


def _form_active(native: Any) -> bool:
    return bool(native.Visible) and str(native.WindowState) != "Minimized"


def trim_if_bloated(window: Any, beat: dict[str, Any]) -> bool:
    """Heartbeat hook: reload the hidden main page once its WebView2 grew too big. The open
    chats come back (mark_crash_reload), and it is activity, not an error. True when it did."""
    if not enabled():
        return False
    native = getattr(window, "native", None)
    try:
        if native is None or native.IsDisposed:
            return False
        now = time.monotonic()
        if not should_trim(beat, active=_form_active(native), now=now, last_at=_last_trim["at"]):
            return False
        _last_trim["at"] = now
        gb = float(beat.get("webview_gb") or 0.0)

        def reload() -> None:
            if native.IsDisposed or _form_active(native):
                return  # shown again meanwhile: leave the page alone
            from frontend.error_log import record_activity
            from frontend.ui_web.panel_api import mark_crash_reload

            for control in native.Controls:
                core = getattr(control, "CoreWebView2", None)
                if core is not None:
                    mark_crash_reload()
                    core.Reload()
                    record_activity("webview", f"WebView2 used {gb:.1f} GB while the window was hidden; reloaded it to free memory")
                    return

        from System import Action

        native.BeginInvoke(Action(reload))
        return True
    except Exception:
        _log.debug("WebView memory trim unavailable", exc_info=True)
        return False


def update_form(native: Any) -> None:
    """Read current visibility at dispatch time, including native browser panes."""
    if native.IsDisposed:
        return
    active = bool(native.Visible) and str(native.WindowState) != "Minimized"
    for control in native.Controls:
        update_control(control, active=active and bool(control.Visible))


def install(window: Any) -> None:
    """Track hide/show and minimize/restore without relying on a JS heartbeat.

    pywebview does not expose a hidden event. Its shown/loaded callbacks run on
    worker threads, so hook the native form asynchronously once it exists.
    """
    if not enabled():
        return
    native = None
    closed = False
    last_active = None

    def changed(_sender: Any, _event: Any) -> None:
        nonlocal last_active
        if not closed and native is not None:
            active = bool(native.Visible) and str(native.WindowState) != "Minimized"
            # Resize fires for every drag pixel. Only a visibility/state change
            # needs COM calls; browser tab visibility is handled at its control.
            if active != last_active:
                update_form(native)
                last_active = active

    def attach() -> None:
        nonlocal native
        form = getattr(window, "native", None)
        if closed or form is None or form.IsDisposed:
            return
        if native is None:
            native = form
            form.VisibleChanged += changed
            form.Resize += changed
        update_form(form)

    def schedule() -> None:
        form = getattr(window, "native", None)
        if closed or form is None:
            return
        try:
            if form.InvokeRequired:
                from System import Action

                form.BeginInvoke(Action(attach))
            else:
                attach()
        except Exception:
            _log.debug("WebView memory policy unavailable", exc_info=True)

    def detach() -> None:
        nonlocal closed
        closed = True
        if native is not None:
            try:
                native.VisibleChanged -= changed
                native.Resize -= changed
            except Exception:
                pass
        window.events.shown -= schedule
        window.events.loaded -= schedule
        window.events.closed -= detach

    window.events.shown += schedule
    window.events.loaded += schedule
    window.events.closed += detach
