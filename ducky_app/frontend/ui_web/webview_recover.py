"""Reload WebView2 after the *renderer* dies so the panel is not stuck black.

WebView2 paints ``WEBVIEW2_DEFAULT_BACKGROUND_COLOR`` (#0a0a0a) when the
renderer dies. Without ``ProcessFailed`` → Reload the window stays blank until
the user kills the EXE. Overlay browser panes that die while Visible sit on
top of the app and look the same.

Do **not** Reload on Gpu / Utility / Sandbox exits — those fire during tab
switches and compositor recycle. Reloading remounts React (Checking… forever,
every click stutters).

A busy page is not a dead one either: WebView2 raises RenderProcessUnresponsive
every few seconds while a long script runs or the PC is busy (UEFN compiling,
Fortnite running) and the page usually answers again on its own. Reloading on
the first one threw the whole UI away mid-work — "it freezes, then reloads
everything". Only a page that stays unresponsive for a full minute is reloaded.
"""

from __future__ import annotations

import time
from typing import Any, Callable

_DEBOUNCE_S = 3.0
_last_recover: dict[int, float] = {}
_attached: set[int] = set()

# CoreWebView2ProcessFailedKind: only these leave a dead document.
_RECOVER_KIND_NAMES = frozenset(
    {
        "browserprocessexited",
        "renderprocessexited",
    }
)
_RECOVER_KIND_INTS = frozenset({0, 1})
_UNRESPONSIVE_KIND = "renderprocessunresponsive"
_UNRESPONSIVE_KIND_INT = 2
# Reload a page only after it has stayed unresponsive this long.
_HUNG_RELOAD_AFTER_S = 60.0
# A pause this long between unresponsive events means the page answered in between.
_UNRESPONSIVE_GAP_S = 15.0
# Per WebView: (first, latest) unresponsive event of the current episode.
_unresponsive: dict[int, tuple[float, float]] = {}


def process_fail_kind(event: Any) -> str:
    """Normalize ProcessFailedKind to a short lower name or digit string."""
    raw = getattr(event, "ProcessFailedKind", "") if event is not None else ""
    try:
        return str(int(raw))
    except (TypeError, ValueError):
        pass
    name = getattr(raw, "name", None) or str(raw or "")
    return str(name).split(".")[-1].strip().lower()


def _kind_key(kind: str | int | None) -> str | int | None:
    if kind is None or kind is False:
        return None
    try:
        return int(kind)
    except (TypeError, ValueError):
        pass
    k = str(kind).split(".")[-1].strip().lower()
    return int(k) if k.isdigit() else k


def should_recover_process_fail(kind: str | int | None) -> bool:
    """True only when the document process is gone (not GPU/utility recycle,
    not a page that is merely busy)."""
    k = _kind_key(kind)
    if isinstance(k, int):
        return k in _RECOVER_KIND_INTS
    return k in _RECOVER_KIND_NAMES


def is_unresponsive_kind(kind: str | int | None) -> bool:
    k = _kind_key(kind)
    return k == _UNRESPONSIVE_KIND_INT or k == _UNRESPONSIVE_KIND


def hung_long_enough(key: int, now: float, *, label: str = "") -> bool:
    """One more RenderProcessUnresponsive for ``key``: True once the page has
    stayed unresponsive for _HUNG_RELOAD_AFTER_S without answering in between."""
    first, last = _unresponsive.get(key, (0.0, 0.0))
    if not first or now - last > _UNRESPONSIVE_GAP_S:
        first = now
        try:
            # Activity, not error: errors drop a repeat of the last line, and how
            # often the page goes busy is the thing worth seeing.
            from frontend.error_log import record_activity

            record_activity(
                "webview",
                f"WebView2 page busy ({label or 'unknown'}); waiting for it instead of reloading",
            )
        except Exception:
            pass
    _unresponsive[key] = (first, now)
    if now - first < _HUNG_RELOAD_AFTER_S:
        return False
    _unresponsive.pop(key, None)
    return True


def recover_core_webview(core: Any, *, reason: str = "") -> dict[str, Any]:
    """Reload (or re-Navigate) a live CoreWebView2. Debounced per instance."""
    if core is None:
        return {"ok": False, "error": "no core"}
    key = id(core)
    now = time.monotonic()
    if now - _last_recover.get(key, 0.0) < _DEBOUNCE_S:
        return {"ok": True, "action": "debounced"}
    _last_recover[key] = now
    try:
        from frontend.error_log import record_error

        record_error("webview", f"WebView2 process failed ({reason or 'unknown'}); reloading")
    except Exception:
        pass
    try:
        core.Reload()
        return {"ok": True, "action": "reload"}
    except Exception as exc:
        try:
            src = str(getattr(core, "Source", "") or "")
            if src:
                core.Navigate(src)
                return {"ok": True, "action": "navigate"}
        except Exception:
            pass
        return {"ok": False, "error": str(exc)}


def attach_process_failed(
    core: Any,
    *,
    label: str = "main",
    on_fail: Callable[[str], None] | None = None,
) -> Any | None:
    """Subscribe ``ProcessFailed`` once. Keep the returned handler referenced."""
    if core is None:
        return None
    key = id(core)
    if key in _attached:
        return None

    def _on_fail(_sender: Any, event: Any) -> None:
        kind = process_fail_kind(event)
        if is_unresponsive_kind(kind):
            if not hung_long_enough(key, time.monotonic(), label=label):
                return
        elif not should_recover_process_fail(kind):
            return
        _unresponsive.pop(key, None)
        reason = f"{label}:{kind}" if kind else label
        # Overlay panes: hide first so a dead control cannot cover the app.
        if on_fail is not None:
            try:
                on_fail(reason)
            except Exception:
                pass
        recover_core_webview(core, reason=reason)

    try:
        core.ProcessFailed += _on_fail
        _attached.add(key)
    except Exception:
        return None
    return _on_fail


def nudge_webview_visible(window: Any) -> None:
    """After hide-to-tray, make the WinForms WebView2 paint again.

    Occlusion can drop the compositor; a black DefaultBackgroundColor pane is
    left until Visible/Refresh. Does not Reload (that remounts React).
    """
    native = getattr(window, "native", None)
    ctrl = getattr(native, "webview", None) if native is not None else None
    if ctrl is None:
        return

    def _go() -> None:
        try:
            ctrl.Visible = True
        except Exception:
            pass
        try:
            ctrl.Refresh()
        except Exception:
            pass

    try:
        from frontend.ui_web.win_frameless import _run_on_form_ui

        _run_on_form_ui(native, _go)
    except Exception:
        try:
            _go()
        except Exception:
            pass
