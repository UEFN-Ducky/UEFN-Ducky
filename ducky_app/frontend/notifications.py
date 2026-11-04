"""Alerts: the things in the header's activity list worth telling you about.

An alert is something that needs you (a question, a command to approve), something
that failed, long work that finished (``LONG_WORK_S`` or more), or a workflow's
Message me. Quick replies, single commands, short runs, sub-agents and the steps
inside a workflow stay in the activity list only; their callers never alert.

Each alert:

- shows a Windows notification from the tray icon unless a Ducky window is in front.
  Alerts close together merge into one notification ("3 updates"), never a burst;
- joins the next batch for the account (uefnducky.org). A batch is one request, holds
  at most one row per chat or workflow (the newest wins), and the profile's bell lists
  them. It pushes the phone only for alerts that came while you were away from this
  PC (no keyboard or mouse for ``AWAY_S``).

Only a name and a short status line leave the PC. ``detail`` (the question, the
command, the message) shows in the Windows notification only.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import threading
import time
from typing import Any, Callable

_log = logging.getLogger(__name__)

LONG_WORK_S = 20.0
AWAY_S = 120.0
# The same alert (same chat or workflow, same status) inside this window is one alert:
# a second window reporting the same install, a chat asking twice in a row.
SAME_ALERT_S = 60.0
# After a Windows notification, the ones that follow inside this gap wait and show as one.
TOAST_GAP_S = 6.0
# The first alert of a batch waits this long for others before the batch goes out.
SITE_BATCH_S = 8.0
SITE_BATCH_MAX = 20

LEVELS = ("ok", "error", "ask", "info")
KINDS = ("chat", "workflow")

ToastFn = Callable[[str, str, Callable[[], None] | None], bool]
OpenFn = Callable[[str, str], None]

_toast_fn: ToastFn | None = None
_open_fn: OpenFn | None = None

_lock = threading.Lock()
_recent: dict[str, float] = {}
_toast_last = 0.0
_toast_pending: list[dict[str, str]] = []
_toast_timer: threading.Timer | None = None
_site_items: dict[str, dict[str, Any]] = {}
_site_timer: threading.Timer | None = None


def bind_desktop(toast: ToastFn | None, open_target: OpenFn | None) -> None:
    """The app's tray shows notifications; ``open_target(kind, id)`` brings the window up on
    that chat or workflow (``kind`` "" just brings it up). Set once by the panel process."""
    global _toast_fn, _open_fn
    _toast_fn = toast
    _open_fn = open_target


def _app_in_front() -> bool:
    """A window of this process (the app or a floating chat) is the foreground window."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value) == os.getpid()
    except Exception:
        return False


def _idle_seconds() -> float | None:
    """Seconds since the last keyboard or mouse input on this PC (None: unknown)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        return ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return None


def _clip(text: str, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _first_time(key: str, now: float) -> bool:
    with _lock:
        for k in [k for k, at in _recent.items() if now - at > SAME_ALERT_S]:
            _recent.pop(k, None)
        if key in _recent:
            return False
        _recent[key] = now
        return True


def alert(
    title: str,
    status: str,
    *,
    level: str = "ok",
    kind: str = "",
    target_id: str = "",
    detail: str = "",
) -> bool:
    """Announce one alert (see the module docstring). Never blocks or raises.

    ``title`` names the chat, workflow or job; ``status`` is the short line that may
    leave the PC ("Finished.", "Needs your answer."); ``detail`` stays on this PC.
    False when it repeated an alert from the last ``SAME_ALERT_S``."""
    try:
        level = level if level in LEVELS else "info"
        kind = kind if kind in KINDS and target_id else ""
        target_id = str(target_id or "").strip()[:80] if kind else ""
        title = _clip(title, 80) or "UEFN Ducky"
        status = _clip(status, 160)
        detail = str(detail or "").strip()
        digest = hashlib.sha1(detail.encode("utf-8", "replace")).hexdigest()[:8] if detail else ""
        now = time.monotonic()
        if not _first_time(f"{kind}:{target_id or title}:{level}:{status}:{digest}", now):
            return False
        in_front = _app_in_front()
        idle = _idle_seconds()
        away = idle is None or idle >= AWAY_S
        if not in_front:
            _queue_toast({"title": title, "text": _clip(detail, 240) or status, "kind": kind, "id": target_id, "level": level})
        _queue_site({
            "title": title,
            "body": status,
            "level": level,
            "kind": kind,
            "id": target_id,
            "push": away,
            "at": int(time.time()),
        })
        return True
    except Exception:
        _log.debug("alert failed", exc_info=True)
        return False


# --- Windows notifications -------------------------------------------------------


def _bridge_forwarder() -> Callable[[dict[str, Any]], None] | None:
    """In the MCP bridge process (agent_modes loaded, no panel): its sender to the panel.
    Looked up, never imported: an alert must not pull in the agent stack."""
    modes = sys.modules.get("frontend.ui_web.agent_turns.push")
    try:
        if modes is not None and modes._in_bridge_process():
            return modes._forward_to_panel
    except Exception:
        pass
    return None


def _queue_toast(item: dict[str, str]) -> None:
    global _toast_last, _toast_timer
    forward = _bridge_forwarder() if _toast_fn is None else None
    if forward is not None:
        # The tray lives in the panel process: it shows this one (and merges it there).
        try:
            forward({"type": "os_notification", **item})
        except Exception:
            pass
        return
    show_now = False
    with _lock:
        now = time.monotonic()
        if _toast_timer is None and now - _toast_last >= TOAST_GAP_S:
            _toast_last = now
            show_now = True
        else:
            _toast_pending.append(item)
            if _toast_timer is None:
                _toast_timer = threading.Timer(max(0.5, TOAST_GAP_S - (now - _toast_last)), _flush_toasts)
                _toast_timer.daemon = True
                _toast_timer.start()
    if show_now:
        _show([item])


def show_forwarded(event: dict[str, Any]) -> None:
    """A notification the MCP bridge process sent over (it has no tray)."""
    item = {k: str(event.get(k) or "") for k in ("title", "text", "kind", "id", "level")}
    # The bridge can't tell whether the app's window is in front; this process can.
    if item["title"] and not _app_in_front():
        _queue_toast(item)


def _flush_toasts() -> None:
    global _toast_last, _toast_timer
    with _lock:
        items = list(_toast_pending)
        _toast_pending.clear()
        _toast_timer = None
        _toast_last = time.monotonic()
    if items:
        _show(items)


def _show(items: list[dict[str, str]]) -> None:
    toast = _toast_fn
    if toast is None:
        return
    if len(items) == 1:
        item = items[0]
        title, text = item["title"], item["text"]
        target = (item.get("kind") or "", item.get("id") or "")
    else:
        # Questions first: the one a click should open is the one that needs you.
        ranked = sorted(items, key=lambda i: {"ask": 0, "error": 1}.get(i.get("level") or "", 2))
        title = f"{len(items)} updates"
        text = _clip(" · ".join(f"{i['title']}: {i['text']}" for i in ranked), 240)
        first = ranked[0]
        target = (first.get("kind") or "", first.get("id") or "") if first.get("level") == "ask" else ("", "")
    opener = _open_fn

    def on_click() -> None:
        if opener is not None:
            opener(*target)

    try:
        toast(title, text, on_click)
    except Exception:
        _log.debug("toast failed", exc_info=True)


# --- The account (uefnducky.org) -------------------------------------------------


def _queue_site(item: dict[str, Any]) -> None:
    global _site_timer
    key = f"{item['kind']}:{item['id']}" if item["kind"] else f"title:{item['title']}"
    with _lock:
        prev = _site_items.pop(key, None)
        if prev is not None and prev.get("push"):
            item["push"] = True  # a newer status for it still owes the phone the older push
        _site_items[key] = item
        if _site_timer is None:
            _site_timer = threading.Timer(SITE_BATCH_S, _send_site)
            _site_timer.daemon = True
            _site_timer.start()


def _send_site() -> None:
    global _site_timer
    with _lock:
        batch = list(_site_items.values())[-SITE_BATCH_MAX:]
        _site_items.clear()
        _site_timer = None
    if not batch:
        return
    try:
        from frontend.duckyos_account import send_account_alerts

        send_account_alerts(batch)
    except Exception:
        _log.debug("account alerts failed", exc_info=True)


def flush_for_tests() -> None:
    """Send the pending batch and notifications now (tests)."""
    global _site_timer, _toast_timer
    with _lock:
        timers = [t for t in (_site_timer, _toast_timer) if t is not None]
    for t in timers:
        t.cancel()
    _flush_toasts()
    _send_site()


def reset_for_tests() -> None:
    global _toast_last, _toast_timer, _site_timer
    with _lock:
        for t in (_toast_timer, _site_timer):
            if t is not None:
                t.cancel()
        _toast_timer = _site_timer = None
        _toast_last = 0.0
        _toast_pending.clear()
        _site_items.clear()
        _recent.clear()
