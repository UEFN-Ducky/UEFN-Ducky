"""Answer the launcher's ``call`` messages with the same js_api object the pywebview window uses.

The page in Ducky-Launcher.exe calls ``window.pywebview.api.<name>(...args)``; the launcher
forwards that as ``{"t":"call","id","name","args"}``. HostCalls runs it on the process's one
PanelApi (``shared_panel_api()``, the object webview_app.py passes as ``js_api``) and answers
with a ``result`` message. Which names may run comes from inventory.INVENTORY:

- ``handler == "python"``: run ``getattr(api, name)(*args)``.
- launcher handlers (``window.*`` / ``dialog.*``): ``error: handled_by_launcher`` — the
  launcher owns its window, drag, maximize, close and dialogs and should never forward them.
- not in the lean cut (focus windows, capture): ``error: not_in_lean_cut``.
- anything else: ``error: unknown``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from frontend.native_host import inventory, protocol

log = logging.getLogger(__name__)

ERR_UNKNOWN = "unknown"
ERR_HANDLED_BY_LAUNCHER = "handled_by_launcher"
ERR_NOT_IN_LEAN_CUT = "not_in_lean_cut"
ERR_TOO_LARGE = "result_too_large"


def _shared_api() -> Any:
    from frontend.ui_web.panel_api import shared_panel_api

    return shared_panel_api()


def _jsonable(value: Any) -> Any:
    """What pywebview would hand the page: JSON, with anything odd turned into a string."""
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))


class HostCalls:
    def __init__(self, api: Any = None, *, api_factory: Callable[[], Any] = _shared_api) -> None:
        self._api = api
        self._api_factory = api_factory

    @property
    def api(self) -> Any:
        if self._api is None:
            self._api = self._api_factory()
        return self._api

    def refusal(self, name: object) -> str | None:
        """Why ``name`` won't run here, or None when it will."""
        if not isinstance(name, str) or not name or name.startswith("_") or "." in name:
            return ERR_UNKNOWN
        entry = inventory.INVENTORY.get(name)
        if entry is None:
            return ERR_UNKNOWN
        if inventory.handled_by_launcher(name):
            return ERR_HANDLED_BY_LAUNCHER
        if not entry["lean_cut"] or entry["handler"] != "python":
            return ERR_NOT_IN_LEAN_CUT
        return None

    def dispatch(self, call_id: str, name: str, args: list[Any] | None = None) -> dict[str, Any]:
        """The ``result`` message for one call."""
        refused = self.refusal(name)
        if refused:
            return protocol.result_error(call_id, refused)
        func = getattr(self.api, name, None)
        if not callable(func):
            return protocol.result_error(call_id, ERR_UNKNOWN)
        try:
            value = func(*(args or []))
        except Exception as exc:  # the page sees the same failure pywebview would report
            log.warning("native host call %s failed: %s", name, exc, exc_info=True)
            return protocol.result_error(call_id, f"{type(exc).__name__}: {exc}")
        return protocol.result_ok(call_id, _jsonable(value))

    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """A decoded message in; its result out (None for anything but a call)."""
        if msg.get("t") != "call":
            return None
        return self.dispatch(msg["id"], msg["name"], msg.get("args") or [])

    def handle_line(self, line: str | bytes) -> str | None:
        """One stdin line in; the encoded result line out, or None (not a call, or unreadable)."""
        try:
            msg = protocol.decode(line)
        except protocol.ProtocolError as exc:
            log.warning("native host: bad line ignored: %s", exc)
            return None
        result = self.handle(msg)
        if result is None:
            return None
        try:
            return protocol.encode(result)
        except protocol.ProtocolError:
            return protocol.encode(protocol.result_error(result["id"], ERR_TOO_LARGE))
