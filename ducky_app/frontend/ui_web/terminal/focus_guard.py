"""While an agent's terminal command runs, no window it opens takes the person's focus.

Nov 1 2026: an agent's test program opened WebView2 windows (off-screen, a few seconds
each) and every one took the keyboard while the person typed in Ducky. A program started
from Ducky's terminals inherits Ducky's right to come to the front, so Ducky locks the
foreground (LockSetForegroundWindow) for the command's run. Windows lifts the lock itself
the moment the person presses Alt or clicks another window, so it never traps them.

ponytail: guards commands the agent waits for; a background command's later windows are
the hard rule's job ("No windows on the person's screen").
"""

from __future__ import annotations

import sys
import threading
from contextlib import contextmanager
from typing import Iterator

_LSFW_LOCK = 1
_LSFW_UNLOCK = 2

_mutex = threading.Lock()
_holders = 0


def _set_locked(on: bool) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        # Succeeds only when Ducky is the foreground process; otherwise the person is in another
        # app, and a new window can't take the focus from there anyway.
        return bool(ctypes.windll.user32.LockSetForegroundWindow(_LSFW_LOCK if on else _LSFW_UNLOCK))
    except Exception:
        return False


@contextmanager
def no_focus_steal() -> Iterator[None]:
    """Nested and concurrent runs share one lock; the last one out unlocks."""
    global _holders
    with _mutex:
        _holders += 1
        if _holders == 1:
            _set_locked(True)
    try:
        yield
    finally:
        with _mutex:
            _holders -= 1
            if _holders == 0:
                _set_locked(False)
