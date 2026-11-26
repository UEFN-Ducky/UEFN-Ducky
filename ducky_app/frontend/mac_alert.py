"""macOS message boxes: an NSAlert on the main thread (Tk can't share it with Cocoa)."""

from __future__ import annotations

import threading
from collections.abc import Sequence


def ask(title: str, message: str, buttons: Sequence[str] = ("OK",), *, wait_s: float = 600.0) -> int:
    """Show a modal alert; the index of the button pressed, or -1 (no AppKit, no answer).

    Safe from any thread: off the main thread it runs through ``AppHelper.callAfter`` and
    waits for the answer. Before the Cocoa loop runs, call it from the main thread."""
    try:
        from AppKit import NSAlert, NSAlertFirstButtonReturn, NSApplication  # type: ignore[import-not-found]
        from Foundation import NSThread  # type: ignore[import-not-found]
        from PyObjCTools import AppHelper  # type: ignore[import-not-found]
    except ImportError:
        return -1
    out = {"i": -1}
    done = threading.Event()

    def run() -> None:
        try:
            app = NSApplication.sharedApplication()
            alert = NSAlert.alloc().init()
            alert.setMessageText_(title)
            alert.setInformativeText_(message)
            alert.setAlertStyle_(2)  # NSAlertStyleCritical
            for label in buttons:
                alert.addButtonWithTitle_(label)
            app.activateIgnoringOtherApps_(True)
            out["i"] = int(alert.runModal()) - int(NSAlertFirstButtonReturn)
        except Exception:
            out["i"] = -1
        finally:
            done.set()

    if NSThread.isMainThread():
        run()
    else:
        AppHelper.callAfter(run)
        done.wait(wait_s)
    return out["i"]
