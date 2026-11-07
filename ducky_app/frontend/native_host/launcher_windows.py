"""Focus windows under Ducky-Launcher.exe: the launcher draws them, Python keeps a stand-in.

``--backend`` has no pywebview, so ``focus_windows`` gets a ``LauncherWindow`` per wid that
asks the launcher (events ``window_open`` / ``window_raise`` / ``window_close``) instead.
"""

from __future__ import annotations

import os
from typing import Any, Callable

SendEvent = Callable[[str, dict[str, Any]], Any]


def active() -> bool:
    """True in ``--backend`` under the launcher (it sets ``UEFN_DUCKY_NATIVE_CHILD=1``)."""
    from frontend.native_switch import NATIVE_CHILD_ENV

    return os.environ.get(NATIVE_CHILD_ENV) == "1"


def sender_of(main_window: Any) -> SendEvent | None:
    """The launcher's event writer behind ``lifecycle.BackendWindow`` (its dialog broker's)."""
    # ponytail: borrows the dialog broker's writer; give BackendWindow a public send if a third user appears
    send = getattr(getattr(main_window, "_dialogs", None), "_send_event", None)
    return send if callable(send) else None


class _Events:
    """``window.events.<name> += handler`` is a no-op: the launcher owns the real window."""

    def __getattr__(self, _name: str) -> _Events:
        return self

    def __iadd__(self, _handler: object) -> _Events:
        return self

    def __isub__(self, _handler: object) -> _Events:
        return self


class LauncherWindow:
    """One focus window the launcher shows. Any other pywebview call is a no-op returning None."""

    def __init__(self, send: SendEvent, wid: str, url: str, title: str,
                 x: int | None, y: int | None, width: int, height: int) -> None:
        self._send = send
        self.wid = wid
        self.title = title
        self.events = _Events()
        self._send("window_open", {"wid": wid, "url": url, "title": title,
                                   "x": x, "y": y, "w": int(width), "h": int(height)})

    def show(self) -> None:
        self._send("window_raise", {"wid": self.wid})

    def restore(self) -> None:
        return None  # show() already raised it

    def destroy(self) -> None:
        self._send("window_close", {"wid": self.wid})

    def evaluate_js(self, _js: str) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *_a, **_k: None
