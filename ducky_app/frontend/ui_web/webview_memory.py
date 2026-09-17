"""Best-effort WebView2 memory targets; never suspend scripts or reload pages."""

from __future__ import annotations

import logging
import os
import sys
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
