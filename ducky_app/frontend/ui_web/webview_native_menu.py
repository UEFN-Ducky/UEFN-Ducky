"""Right-click on text you can edit: WebView2's own menu (the spell checker's suggestions,
Add to dictionary, Cut / Copy / Paste) drawn as Ducky's menu.

The suggestions come from the browser's spell checker, never a word list of ours: the
menu WebView2 builds is sent to the page, the page shows it in Ducky's style, and the
pick goes back as ``SelectedCommandId`` so WebView2 runs it itself (WebView2's
"create your own context-menu UI" pattern: Handled + deferral).
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from typing import Any

_log = logging.getLogger(__name__)

# Browser-page commands that make no sense in Ducky's own text boxes.
_DROP = {
    "back", "forward", "reload", "saveAs", "print", "createQrCode", "inspectElement",
    "share", "webCapture", "openLinkInNewWindow", "saveLinkAs", "copyLinkLocation",
}
# A menu left open (the page closed, a crash) gives WebView2 its event back.
_PICK_TIMEOUT_S = 60.0

_lock = threading.Lock()
_pending: dict[str, tuple[Any, Any, Any, threading.Timer]] = {}


def _kind(item: Any) -> str:
    return str(getattr(item, "Kind", "")).rsplit(".", 1)[-1]


def menu_items(items: Any) -> list[dict[str, Any]]:
    """The commands to show, in WebView2's order; separators kept between groups only."""
    out: list[dict[str, Any]] = []
    for item in items or []:
        kind = _kind(item)
        if kind == "Separator":
            if out and not out[-1].get("separator"):
                out.append({"separator": True})
            continue
        if kind != "Command" or str(getattr(item, "Name", "")) in _DROP:
            continue
        label = str(getattr(item, "Label", "") or "").replace("&&", "\0").replace("&", "").replace("\0", "&")
        if not label.strip():
            continue
        out.append({
            "id": int(getattr(item, "CommandId")),
            "label": label,
            "enabled": bool(getattr(item, "IsEnabled", True)),
        })
    while out and out[-1].get("separator"):
        out.pop()
    return out


def attach(core: Any, window: Any, form: Any, *, hide_others: bool) -> Any:
    """Hook one window's CoreWebView2 (UI thread). ``hide_others``: no native menu
    anywhere else (the Store EXE); otherwise the rest keep WebView2's own menu."""

    def on_requested(_sender: Any, args: Any) -> None:
        try:
            target = args.ContextMenuTarget
            items = menu_items(args.MenuItems) if bool(getattr(target, "IsEditable", False)) else []
            if not any("id" in i for i in items):
                if hide_others:
                    args.Handled = True
                return
            token = uuid.uuid4().hex
            deferral = args.GetDeferral()
            args.Handled = True
            timer = threading.Timer(_PICK_TIMEOUT_S, lambda: pick(token, -1))
            timer.daemon = True
            with _lock:
                _pending[token] = (args, deferral, form, timer)
            timer.start()
            from frontend.ui_web.ui_dispatch import schedule_evaluate_js

            payload = json.dumps({"type": "native_menu", "token": token, "items": items}, ensure_ascii=False)
            schedule_evaluate_js(window, f"window.__uefnPanelPush && window.__uefnPanelPush({payload})")
        except Exception:
            _log.debug("native menu failed", exc_info=True)

    core.ContextMenuRequested += on_requested
    return on_requested


def keep_menu_events(settings: Any) -> None:
    """After pywebview's own ``on_webview_ready``: it sets AreDefaultContextMenusEnabled to its
    debug flag (False in every shipped build), and with default menus off WebView2 never raises
    ContextMenuRequested, so right-click showed no spelling fixes at all (Oct 29 2026). Once
    ``attach`` hooked the event it draws or hides every menu itself, so turn them back on."""
    if settings is not None:
        settings.AreDefaultContextMenusEnabled = True


def pick(token: str, command_id: int) -> bool:
    """The page's answer: run ``command_id`` (a spelling fix, Paste...) or nothing (-1)."""
    with _lock:
        entry = _pending.pop(str(token or ""), None)
    if entry is None:
        return False
    args, deferral, form, timer = entry
    timer.cancel()

    def finish() -> None:
        try:
            if int(command_id) >= 0:
                args.SelectedCommandId = int(command_id)
        except Exception:
            _log.debug("native menu pick failed", exc_info=True)
        try:
            deferral.Complete()
        except Exception:
            pass

    try:
        if form is not None and bool(getattr(form, "InvokeRequired", False)):
            from System import Action

            form.BeginInvoke(Action(finish))
        else:
            finish()
    except Exception:
        finish()
    return True
