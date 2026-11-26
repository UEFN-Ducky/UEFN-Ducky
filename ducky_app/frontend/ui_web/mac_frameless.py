"""macOS window chrome (pywebview Cocoa): our header under a transparent title bar, with the
native traffic lights kept, and header drags moved by a Cocoa tracking loop.

pywebview's frameless Cocoa window is already full-size-content with a transparent title bar;
it only hides the three buttons, so :func:`apply` shows them again."""

from __future__ import annotations

from typing import Any

FULL_SIZE_CONTENT = 1 << 15  # NSWindowStyleMaskFullSizeContentView
TITLE_HIDDEN = 1  # NSWindowTitleHidden
TRAFFIC_LIGHTS = (0, 1, 2)  # NSWindowCloseButton, MiniaturizeButton, ZoomButton
_LEFT_MOUSE_UP = 2  # NSEventTypeLeftMouseUp
_DRAG_MASK = (1 << 2) | (1 << 6)  # LeftMouseUp | LeftMouseDragged


def apply_native(ns_window: Any) -> None:
    """Main thread: full-size content, transparent hidden title, traffic lights shown."""
    ns_window.setStyleMask_(int(ns_window.styleMask()) | FULL_SIZE_CONTENT)
    ns_window.setTitlebarAppearsTransparent_(True)
    ns_window.setTitleVisibility_(TITLE_HIDDEN)
    for kind in TRAFFIC_LIGHTS:
        button = ns_window.standardWindowButton_(kind)
        if button is not None:
            button.setHidden_(False)


def apply(window: Any) -> bool:
    """Any thread: :func:`apply_native` on the window's NSWindow in the Cocoa loop."""
    native = getattr(window, "native", None)
    if native is None:
        return False
    try:
        from PyObjCTools import AppHelper  # type: ignore[import-not-found]
    except ImportError:
        return False
    AppHelper.callAfter(apply_native, native)
    return True


def bring_to_front(window: Any) -> bool:
    """Any thread: make the app active and this window key, above other apps' windows."""
    native = getattr(window, "native", None)
    if native is None:
        return False
    try:
        import AppKit  # type: ignore[import-not-found]
        from PyObjCTools import AppHelper  # type: ignore[import-not-found]
    except ImportError:
        return False

    def op() -> None:
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        native.makeKeyAndOrderFront_(None)

    AppHelper.callAfter(op)
    return True


def cursor_point() -> tuple[int, int] | None:
    """The mouse in pywebview's window coordinates: points, top-left of the main screen,
    y down (Cocoa's own origin is bottom-left, y up)."""
    try:
        import AppKit  # type: ignore[import-not-found]
    except ImportError:
        return None
    screens = AppKit.NSScreen.screens()
    if not screens:
        return None
    p = AppKit.NSEvent.mouseLocation()
    return int(p.x), int(screens[0].frame().size.height - p.y)


def set_bounds(window: Any, x: int, y: int, width: int, height: int) -> bool:
    """Move and size through pywebview, which takes top-left points on Cocoa too."""
    if getattr(window, "native", None) is None:
        return False
    window.resize(int(width), int(height))
    window.move(int(x), int(y))
    return True


def begin_move(window: Any) -> bool:
    """Main thread (the JS bridge's script-message callback): move the window with the mouse
    until the left button is up. The page's mouse-up may already be gone, so the button state
    is polled too, never only the event."""
    ns = getattr(window, "native", None)
    if ns is None:
        return False
    import AppKit  # type: ignore[import-not-found]

    event = AppKit.NSEvent
    if not int(event.pressedMouseButtons()) & 1:
        return False
    start = event.mouseLocation()
    origin = ns.frame().origin
    ox, oy = float(origin.x), float(origin.y)
    # ponytail: a nested tracking loop inside the bridge callback; a native drag via
    # performWindowDragWithEvent_ needs the original mouse-down, which WebKit already took.
    while int(event.pressedMouseButtons()) & 1:
        ev = ns.nextEventMatchingMask_untilDate_inMode_dequeue_(
            _DRAG_MASK, AppKit.NSDate.dateWithTimeIntervalSinceNow_(0.05), AppKit.NSEventTrackingRunLoopMode, True,
        )
        p = event.mouseLocation()
        ns.setFrameOrigin_((ox + float(p.x) - float(start.x), oy + float(p.y) - float(start.y)))
        if ev is not None and int(ev.type()) == _LEFT_MOUSE_UP:
            break
    return True
