"""Best-effort WebView2 memory, in two levels; never suspend scripts (chats stream while hidden).

Soft: ``MemoryUsageTargetLevel = Low`` while hidden or minimized, while open and untouched for
SOFT_IDLE_S past SOFT_GB, and at once on ``trim_now``. WebView2 never sets it back by itself, so
showing or using the window sets ``Normal`` again. Reload (``trim_if_bloated``): hidden past
TRIM_GB even while agents run, or open but untouched for OPEN_IDLE_S past OPEN_TRIM_GB; a window
in use is never reloaded."""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any

_log = logging.getLogger(__name__)


def enabled() -> bool:
    return sys.platform == "win32" and os.environ.get("UEFN_DUCKY_LOW_MEMORY", "1") != "0"


def _controller(control: Any) -> Any:
    """The control's CoreWebView2Controller: the WinForms control keeps it in a non-public
    field (no public property), so read it by reflection; None when this SDK has none."""
    found = getattr(control, "CoreWebView2Controller", None)
    if found is not None:
        return found
    try:
        from System.Reflection import BindingFlags

        flags = BindingFlags.NonPublic | BindingFlags.Instance
        for name in ("_coreWebView2Controller", "coreWebView2Controller"):
            field = control.GetType().GetField(name, flags)
            if field is not None:
                return field.GetValue(control)
    except Exception:
        pass
    return None


def update_control(control: Any, *, active: bool) -> bool:
    """Called on the WinForms UI thread. Old SDKs and disposed controls are safe.

    Off screen (hidden or minimized) the page's controller is also made invisible: WebView2
    then stops rendering and throttles its timers; agent events still reach the page, and it
    shows them all when it is visible again. Back on screen both are set back."""
    if not enabled():
        return False
    try:
        controller = _controller(control)
        if controller is not None and bool(controller.IsVisible) != bool(active):
            controller.IsVisible = bool(active)
    except Exception:
        pass
    return _set_level(control, low=not active)


def _set_level(control: Any, *, low: bool) -> bool:
    """Only the memory target; rendering stays as it is (the soft level of an open window)."""
    try:
        core = control.CoreWebView2
        current = core.MemoryUsageTargetLevel
        # Use the enum supplied by the loaded SDK; importing a newer assembly is
        # unnecessary and would make older packaged runtimes fail at startup.
        target = getattr(type(current), "Low" if low else "Normal")
        if current != target:
            core.MemoryUsageTargetLevel = target
        return True
    except Exception:
        return False


# Budget 500 MB. Nov 5 2026: the renderer held 978 MB plus 331 MB GPU during a team run, and the
# old 2.0 / 2.5 GB reloads waited for 0 agent turns, so with agents always running they never fired.
SOFT_GB = 1.0
SOFT_IDLE_S = 2 * 60
TRIM_GB = 1.5
TRIM_EVERY_S = 30 * 60
OPEN_TRIM_GB = 1.5
OPEN_IDLE_S = 10 * 60
_last_trim = {"at": 0.0}
# When the soft level was set on an open window (monotonic s); 0 = not set.
_soft = {"at": 0.0}


def input_idle_s() -> float:
    """Seconds since the person last used mouse or keyboard on this PC; 0 when unknown."""
    if sys.platform != "win32":
        return 0.0
    try:
        import ctypes
        from ctypes import wintypes

        class _LastInput(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        info = _LastInput(ctypes.sizeof(_LastInput), 0)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        # Both are 32-bit millisecond tick counts: the mask keeps a wrap at 49.7 days right.
        return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return 0.0


def _gb(beat: dict[str, Any]) -> float:
    try:
        return float(beat.get("webview_gb") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def should_trim(beat: dict[str, Any], *, active: bool, now: float, last_at: float, idle_s: float = 0.0) -> bool:
    """Reload when nobody is using the window, agent turns or not (mark_crash_reload brings the
    open chats back): not within TRIM_EVERY_S of the last time, and either hidden or minimized
    past TRIM_GB, or open with no input for OPEN_IDLE_S past OPEN_TRIM_GB."""
    webview_gb = _gb(beat)
    big = webview_gb >= OPEN_TRIM_GB and idle_s >= OPEN_IDLE_S if active else webview_gb >= TRIM_GB
    return big and now - last_at >= TRIM_EVERY_S


def soft_level(beat: dict[str, Any], *, active: bool, idle_s: float, now: float, low_at: float) -> str:
    """What an open window's memory target should become: "Low" (untouched for SOFT_IDLE_S
    past SOFT_GB), "Normal" (used since it was set Low), or "" (leave it). Hidden or minimized
    windows are Low already (update_form)."""
    if not active:
        return ""
    if low_at:
        return "Normal" if idle_s < now - low_at else ""
    return "Low" if _gb(beat) >= SOFT_GB and idle_s >= SOFT_IDLE_S else ""


def _on_ui(native: Any, fn: Any) -> None:
    if native.InvokeRequired:
        from System import Action

        native.BeginInvoke(Action(fn))
    else:
        fn()


def _lower(native: Any) -> None:
    if not native.IsDisposed:
        for control in native.Controls:
            _set_level(control, low=True)


def trim_now(window: Any, reason: str = "") -> bool:
    """Set the soft level (Low) on every page now, without a reload; using or showing the
    window sets Normal again. True when it was asked."""
    if not enabled():
        return False
    native = getattr(window, "native", None)
    try:
        if native is None or native.IsDisposed:
            return False
        _soft["at"] = time.monotonic()
        _on_ui(native, lambda: _lower(native))
        _log.info("WebView2 set to low memory%s", f": {reason}" if reason else "")
        return True
    except Exception:
        _log.debug("WebView memory trim unavailable", exc_info=True)
        return False


def _apply_soft(native: Any, beat: dict[str, Any], *, active: bool, idle_s: float, now: float) -> None:
    level = soft_level(beat, active=active, idle_s=idle_s, now=now, low_at=_soft["at"])
    if level == "Low":
        _soft["at"] = now
        _on_ui(native, lambda: _lower(native))
    elif level == "Normal":
        _on_ui(native, lambda: update_form(native))  # clears _soft


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
        active, idle_s = _form_active(native), input_idle_s()
        _apply_soft(native, beat, active=active, idle_s=idle_s, now=now)
        if not should_trim(beat, active=active, now=now, last_at=_last_trim["at"], idle_s=idle_s):
            return False
        _last_trim["at"] = now
        gb = float(beat.get("webview_gb") or 0.0)

        def reload() -> None:
            open_now = not native.IsDisposed and _form_active(native)
            if native.IsDisposed or (open_now and input_idle_s() < OPEN_IDLE_S):
                return  # shown again or used meanwhile: leave the page alone
            from frontend.error_log import record_activity
            from frontend.ui_web.panel_api import mark_crash_reload

            for control in native.Controls:
                core = getattr(control, "CoreWebView2", None)
                if core is not None:
                    mark_crash_reload()
                    core.Reload()
                    where = "while nobody used the window" if open_now else "while the window was hidden"
                    record_activity("webview", f"WebView2 used {gb:.1f} GB {where}; reloaded it to free memory")
                    return

        from System import Action

        native.BeginInvoke(Action(reload))
        return True
    except Exception:
        _log.debug("WebView memory trim unavailable", exc_info=True)
        return False


def update_form(native: Any) -> None:
    """Read current visibility at dispatch time, including native browser panes. Sets each
    page's level from it, so an earlier soft level is over."""
    if native.IsDisposed:
        return
    _soft["at"] = 0.0
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
    hooked: list[tuple[Any, str]] = []  # (form or control, event name) carrying ``used``

    def used(_sender: Any, _event: Any) -> None:
        # The person came back (window activated, page focused): Normal now, not on the next
        # heartbeat. The heartbeat in trim_if_bloated stays as the fallback.
        if not closed and native is not None and _soft["at"]:
            update_form(native)

    def hook(target: Any, name: str) -> None:
        try:
            event = getattr(target, name)
            event += used
            setattr(target, name, event)
            hooked.append((target, name))
        except Exception:
            pass  # this control has no such event

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
            hook(form, "Activated")
        for control in form.Controls:
            if not any(target is control for target, _ in hooked):
                hook(control, "GotFocus")
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
        for target, name in hooked:
            try:
                event = getattr(target, name)
                event -= used
                setattr(target, name, event)
            except Exception:
                pass
        hooked.clear()
        window.events.shown -= schedule
        window.events.loaded -= schedule
        window.events.closed -= detach

    window.events.shown += schedule
    window.events.loaded += schedule
    window.events.closed += detach
